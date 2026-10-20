"""tapo-power: switch TP-Link Tapo plugs/switches on and off.

Designed to be driven by an LLM agent: every invocation prints exactly one
JSON object to stdout and exits with a meaningful status code.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import ipaddress
import json
import os
import re
import sys
from typing import Any

from . import __version__, backends
from .backends import AuthError, BackendError, Socket, Switch
from .config import (
    Config,
    ConfigError,
    alias_for,
    default_config_path,
    load_config,
    save_config,
)

EXIT_OK = 0
EXIT_DEVICE = 1
EXIT_USAGE = 2
EXIT_AUTH = 3


class CliError(Exception):
    def __init__(self, message: str, code: int = EXIT_DEVICE):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------
# Device helpers
# --------------------------------------------------------------------------


# A dotted DNS name with no spaces, e.g. "plug.lan" or "kitchen.home.arpa".
HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+\.?$")


def is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def describe(sw: Switch, socket: Socket | None = None) -> dict[str, Any]:
    info: dict[str, Any] = {
        "name": sw.name,
        "ip": sw.ip,
        "model": sw.model,
        "on": sw.on,
    }
    if socket is not None:
        info["child"] = socket.name
        info["on"] = socket.on
    if sw.children:
        info["children"] = [
            {"index": s.index, "name": s.name, "on": s.on} for s in sw.children
        ]
    return info


def find_child(sw: Switch, selector: str) -> Socket:
    if not sw.children:
        raise CliError(f"device '{sw.name}' has no child sockets", EXIT_USAGE)
    wanted = selector.strip().lower()
    for s in sw.children:
        if (s.name or "").lower() == wanted:
            return s
    if selector.isdigit():
        index = int(selector)
        if 1 <= index <= len(sw.children):
            return sw.children[index - 1]
    names = ", ".join(f"{s.index}={s.name}" for s in sw.children)
    raise CliError(
        f"no child '{selector}' on '{sw.name}' (available: {names})", EXIT_USAGE
    )


async def close_all(switches: list[Switch]) -> None:
    await asyncio.gather(*(s.close() for s in switches), return_exceptions=True)


def auth_messages(found: backends.Discovery) -> list[str]:
    return [f"authentication failed: {e}" for e in found.auth_errors]


async def resolve(
    target: str, cfg: Config, creds: tuple[str, str], timeout: int
) -> tuple[Switch, Socket | None]:
    """Resolve a config alias, IP/hostname, or device name to a device."""
    host = cfg.devices.get(target.lower())
    if host is None and is_ip(target):
        host = target
    if host is None and HOSTNAME_RE.match(target):
        try:
            await backends.resolve_ip(target)
            host = target
        except BackendError:
            pass  # Not a resolvable hostname: treat it as a device name.
    if host is not None:
        return await backends.connect(host, *creds, timeout), None

    # Fall back to LAN discovery, matching device or socket names.
    wanted = target.lower()
    found = await backends.discover(*creds, timeout)
    matches: list[tuple[Switch, Socket | None]] = []
    for sw in found.switches:
        if (sw.name or "").lower() == wanted:
            matches.append((sw, None))
        matches += [(sw, s) for s in sw.children if (s.name or "").lower() == wanted]
    keep = matches[0][0] if len(matches) == 1 else None
    await close_all([s for s in found.switches if s is not keep])

    if len(matches) > 1:
        where = ", ".join(
            f"{sw.ip}" + (f" socket {s.index}" if s else "") for sw, s in matches
        )
        raise CliError(
            f"'{target}' matches {len(matches)} devices ({where}); "
            "use an IP address or a unique alias instead",
            EXIT_USAGE,
        )
    if not matches:
        if found.auth_errors and not found.switches:
            raise AuthError(found.auth_errors[0])
        names = sorted({s.name for s in found.switches} | set(cfg.devices))
        hint = f"; known names: {', '.join(names)}" if names else ""
        if found.auth_errors:
            hint += f"; {len(found.auth_errors)} device(s) rejected the login"
        raise CliError(f"no device named '{target}' found{hint}", EXIT_USAGE)
    return matches[0]


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


async def cmd_list(args, cfg, creds) -> dict[str, Any]:
    found = await backends.discover(*creds, args.timeout)
    try:
        if found.auth_errors and not found.switches:
            raise AuthError(found.auth_errors[0])
        aliases = {host: name for name, host in cfg.devices.items()}
        out = []
        for sw in sorted(found.switches, key=lambda s: s.ip):
            info = describe(sw)
            if sw.ip in aliases:
                info["alias"] = aliases[sw.ip]
            out.append(info)
        result: dict[str, Any] = {"ok": True, "action": "list", "devices": out}
        if errors := found.errors + auth_messages(found):
            result["errors"] = errors
        return result
    finally:
        await close_all(found.switches)


async def cmd_status(args, cfg, creds) -> dict[str, Any]:
    sw, socket = await resolve(args.target, cfg, creds, args.timeout)
    try:
        if args.child:
            socket = find_child(sw, args.child)
        return {"ok": True, "action": "status", **describe(sw, socket)}
    finally:
        await sw.close()


async def cmd_switch(args, cfg, creds) -> dict[str, Any]:
    turn_on = args.command == "on"
    sw, socket = await resolve(args.target, cfg, creds, args.timeout)
    try:
        if args.child:
            socket = find_child(sw, args.child)
        await sw.set_power(turn_on, socket)
        await sw.refresh()
        if socket is not None:
            socket = sw.children[socket.index - 1]
            ok = socket.on == turn_on
        elif sw.children:
            # Whole power strip: every socket must have switched.
            ok = all(s.on == turn_on for s in sw.children)
        else:
            ok = sw.on == turn_on
        result = {"ok": ok, "action": args.command, **describe(sw, socket)}
        if not ok:
            result["error"] = f"device did not report {args.command} after command"
        return result
    finally:
        await sw.close()


def assign_aliases(cfg: Config, switches: list[Switch]) -> list[str]:
    """Add an alias per device to `cfg`, keeping aliases unique.

    An existing alias is reused for the same IP, and overwritten when its old
    IP isn't a device found now (the device probably moved address). If a
    different current device holds it, a numeric suffix is added instead.
    """
    current_ips = {s.ip for s in switches}
    assigned: set[str] = set()
    aliases = []
    for sw in switches:
        base = alias = alias_for(sw.name)
        n = 2
        while alias in cfg.devices and cfg.devices[alias] != sw.ip and (
            alias in assigned or cfg.devices[alias] in current_ips
        ):
            alias = f"{base}-{n}"
            n += 1
        cfg.devices[alias] = sw.ip
        assigned.add(alias)
        aliases.append(alias)
    return aliases


async def cmd_setup(args, cfg, creds) -> dict[str, Any]:
    """Verify credentials against the LAN, then save them plus device aliases."""
    found = backends.Discovery()
    if not args.no_discover:
        found = await backends.discover(*creds, args.timeout)
        await close_all(found.switches)
        if found.auth_errors and not found.switches:
            # Every device that answered rejected the login: save nothing.
            raise AuthError(found.auth_errors[0])

    switches = sorted(found.switches, key=lambda s: s.ip)
    cfg.username, cfg.password = creds
    aliases = assign_aliases(cfg, switches)
    devices = [{"alias": a, **describe(sw)} for a, sw in zip(aliases, switches)]
    path = save_config(cfg)

    result: dict[str, Any] = {
        "ok": True,
        "action": "setup",
        "config": str(path),
        "verified": bool(switches),
        "devices": devices,
    }
    if not switches and not args.no_discover:
        result["warning"] = (
            "credentials saved but no devices answered, so the login could not be "
            "verified; check the devices are on this network"
        )
    if errors := found.errors + auth_messages(found):
        result["errors"] = errors
    return result


COMMANDS = {
    "list": cmd_list,
    "status": cmd_status,
    "on": cmd_switch,
    "off": cmd_switch,
    "setup": cmd_setup,
}


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


class JsonArgumentParser(argparse.ArgumentParser):
    """Reports usage errors as JSON too, so agents always get parseable output."""

    def error(self, message: str):
        print(json.dumps({"ok": False, "error": f"{message} (see '{self.prog} --help')"}))
        sys.exit(EXIT_USAGE)


def timeout_seconds(value: str) -> int:
    try:
        seconds = int(value)
    except ValueError:
        seconds = 0
    if not 1 <= seconds <= 60:
        raise argparse.ArgumentTypeError("must be a whole number of seconds from 1 to 60")
    return seconds


def common_options(suppress: bool) -> argparse.ArgumentParser:
    """Options accepted both before and after the subcommand.

    The subcommand copy uses SUPPRESS defaults so it doesn't overwrite
    values given before the subcommand.
    """

    def d(value):
        return argparse.SUPPRESS if suppress else value

    common = JsonArgumentParser(add_help=False)
    common.add_argument(
        "--username", default=d(None), help="Tapo account email (env TAPO_USERNAME)"
    )
    common.add_argument(
        "--password", default=d(None), help="Tapo account password (env TAPO_PASSWORD)"
    )
    common.add_argument(
        "--timeout",
        type=timeout_seconds,
        default=d(10),
        help="network timeout in seconds, 1-60 (default 10)",
    )
    common.add_argument(
        "--text",
        action="store_true",
        default=d(False),
        help="human-readable output instead of JSON",
    )
    return common


def build_parser() -> argparse.ArgumentParser:
    common = common_options(suppress=True)
    parser = JsonArgumentParser(
        prog="tapo-power",
        description="Switch TP-Link Tapo smart plugs/switches on and off. "
        "Prints one JSON object; exit 0=ok, 1=device/network error, "
        "2=usage/config error, 3=authentication failure.",
        parents=[common_options(suppress=False)],
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    setup = sub.add_parser(
        "setup",
        parents=[common],
        help="save your Tapo login and find your devices (run this first)",
    )
    setup.add_argument(
        "--no-discover",
        action="store_true",
        help="just save the credentials, don't log in to any device",
    )
    sub.add_parser("list", parents=[common], help="discover devices on the LAN")
    for name, text in (
        ("status", "show on/off state"),
        ("on", "switch on"),
        ("off", "switch off"),
    ):
        p = sub.add_parser(name, parents=[common], help=text)
        p.add_argument("target", help="config alias, device name, or IP address")
        p.add_argument(
            "--child", help="power-strip socket: name or 1-based index"
        )
    return parser


def resolve_credentials(args, cfg: Config) -> tuple[str, str]:
    user = args.username or os.environ.get("TAPO_USERNAME") or cfg.username
    pw = args.password or os.environ.get("TAPO_PASSWORD") or cfg.password
    if user and pw:
        return user, pw
    if user or pw:
        raise CliError("both username and password are required", EXIT_USAGE)
    raise CliError(
        "Tapo credentials missing: run 'tapo-power setup' once, or set "
        f"TAPO_USERNAME and TAPO_PASSWORD (config file: {cfg.path})",
        EXIT_USAGE,
    )


def prompt_credentials(args) -> tuple[str, str]:
    """Credentials for `setup`: flags, then env vars, then an interactive prompt."""
    user = args.username or os.environ.get("TAPO_USERNAME")
    pw = args.password or os.environ.get("TAPO_PASSWORD")
    if not (user and pw) and not sys.stdin.isatty():
        raise CliError(
            "setup needs a terminal to prompt, or pass --username and --password",
            EXIT_USAGE,
        )
    # Prompts go to stderr so stdout stays a single JSON object.
    try:
        while not user:
            print("Tapo app account email: ", end="", file=sys.stderr, flush=True)
            user = input().strip()
        while not pw:
            pw = getpass.getpass("Tapo app password (hidden): ", stream=sys.stderr)
    except (EOFError, KeyboardInterrupt):
        # isatty() can't be trusted everywhere (Windows reports NUL as a tty).
        print(file=sys.stderr)
        raise CliError(
            "setup cancelled: no input (pass --username and --password to run "
            "non-interactively)",
            EXIT_USAGE,
        ) from None
    return user, pw


def format_text(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return f"error: {result.get('error')}"
    if result["action"] == "list":
        lines = [format_device(d) for d in result["devices"]] or ["no devices found"]
        lines += [f"warning: {e}" for e in result.get("errors", [])]
        return "\n".join(lines)
    if result["action"] == "setup":
        lines = [f"saved login to {result['config']}"]
        lines += [
            f"  {d['alias']}: {format_device(d)}" for d in result["devices"]
        ]
        if result["devices"]:
            first = result["devices"][0]["alias"]
            lines.append(f"try: tapo-power status {first}")
        if "warning" in result:
            lines.append(f"warning: {result['warning']}")
        lines += [f"warning: {e}" for e in result.get("errors", [])]
        return "\n".join(lines)
    return format_device(result)


def format_device(d: dict[str, Any]) -> str:
    name = d["name"] + (f"/{d['child']}" if "child" in d else "")
    line = f"{name} ({d['model']} @ {d['ip']}): {'ON' if d['on'] else 'OFF'}"
    for c in d.get("children", []):
        line += f"\n  [{c['index']}] {c['name']}: {'ON' if c['on'] else 'OFF'}"
    return line


async def run(args) -> tuple[dict[str, Any], int]:
    try:
        if args.command == "setup":
            try:
                cfg = load_config()
            except ConfigError:
                # Let setup repair an unreadable config by starting fresh.
                cfg = Config(path=default_config_path())
            creds = prompt_credentials(args)
        else:
            cfg = load_config()
            creds = resolve_credentials(args, cfg)
        # Hard ceiling so an agent never hangs on an unresponsive network.
        ceiling = args.timeout * 4
        try:
            result = await asyncio.wait_for(
                COMMANDS[args.command](args, cfg, creds), timeout=ceiling
            )
        except TimeoutError:
            raise CliError(
                f"timed out after {ceiling}s; check the device is powered and on "
                "this network, or raise --timeout"
            ) from None
        return result, EXIT_OK if result.get("ok") else EXIT_DEVICE
    except CliError as exc:
        return {"ok": False, "error": str(exc)}, exc.code
    except ConfigError as exc:
        return {"ok": False, "error": str(exc)}, EXIT_USAGE
    except AuthError as exc:
        return {
            "ok": False,
            "error": "authentication failed - check the Tapo account email/password; "
            f"do not retry repeatedly, the device locks after failed logins ({exc})",
        }, EXIT_AUTH
    except BackendError as exc:
        return {"ok": False, "error": str(exc)}, EXIT_DEVICE
    except (TimeoutError, OSError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, EXIT_DEVICE


def main(argv: list[str] | None = None) -> int:
    # Device names can hold emoji/CJK; don't crash on a cp1252 console or pipe.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    result, code = asyncio.run(run(args))
    print(format_text(result) if args.text else json.dumps(result))
    return code


if __name__ == "__main__":
    sys.exit(main())
