"""Human-friendly terminal output: colours, gradient logo, tables, spinners.

Only used when a person runs tapo-power in a terminal (or passes --text).
Agents and scripts get plain JSON, so `rich` is imported lazily to keep
that path fast.
"""

from __future__ import annotations

import sys
from contextlib import nullcontext
from typing import Any

# Matches the project banner: cyan -> teal -> green.
GRADIENT = ("#22d3ee", "#2dd4bf", "#4ade80")
ACCENT = "#2dd4bf"

LOGO_WIDE = """\
████████╗ █████╗ ██████╗  ██████╗       ██████╗  ██████╗ ██╗    ██╗███████╗██████╗
╚══██╔══╝██╔══██╗██╔══██╗██╔═══██╗      ██╔══██╗██╔═══██╗██║    ██║██╔════╝██╔══██╗
   ██║   ███████║██████╔╝██║   ██║█████╗██████╔╝██║   ██║██║ █╗ ██║█████╗  ██████╔╝
   ██║   ██╔══██║██╔═══╝ ██║   ██║╚════╝██╔═══╝ ██║   ██║██║███╗██║██╔══╝  ██╔══██╗
   ██║   ██║  ██║██║     ╚██████╔╝      ██║     ╚██████╔╝╚███╔███╔╝███████╗██║  ██║
   ╚═╝   ╚═╝  ╚═╝╚═╝      ╚═════╝       ╚═╝      ╚═════╝  ╚══╝╚══╝ ╚══════╝╚═╝  ╚═╝"""

LOGO_SMALL = """\
╺┳╸┏━┓┏━┓┏━┓   ┏━┓┏━┓╻ ╻┏━╸┏━┓
 ┃ ┣━┫┣━┛┃ ┃╺━╸┣━┛┃ ┃┃╻┃┣╸ ┣┳┛
 ╹ ╹ ╹╹  ┗━┛   ╹  ┗━┛┗┻┛┗━╸╹┗╸"""

TAGLINE = "switch Tapo smart plugs from the command line"

_consoles: dict[bool, Any] = {}


def console(stderr: bool = False):
    if stderr not in _consoles:
        from rich.console import Console

        _consoles[stderr] = Console(stderr=stderr, highlight=False)
    return _consoles[stderr]


def reset() -> None:
    """Forget cached consoles (tests swap sys.stdout between runs)."""
    _consoles.clear()


def _esc(value: Any) -> str:
    from rich.markup import escape

    return escape(str(value))


# --------------------------------------------------------------------------
# Gradient text
# --------------------------------------------------------------------------


def _hex(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def blend(stops: tuple[str, ...], t: float) -> str:
    """Colour at position t (0..1) along a multi-stop gradient."""
    t = min(max(t, 0.0), 1.0)
    span = t * (len(stops) - 1)
    i = min(int(span), len(stops) - 2)
    a, b = _hex(stops[i]), _hex(stops[i + 1])
    f = span - i
    r, g, bl = (round(x + (y - x) * f) for x, y in zip(a, b))
    return f"#{r:02x}{g:02x}{bl:02x}"


def gradient(text: str, stops: tuple[str, ...] = GRADIENT, bold: bool = True):
    """Colour text left-to-right; multi-line text shares one gradient."""
    from rich.text import Text

    lines = text.splitlines() or [""]
    width = max(len(line) for line in lines)
    out = Text()
    for n, line in enumerate(lines):
        if n:
            out.append("\n")
        for i, ch in enumerate(line):
            colour = blend(stops, i / max(width - 1, 1))
            out.append(ch, style=f"bold {colour}" if bold else colour)
    return out


def banner(subtitle: str = TAGLINE) -> None:
    from rich.padding import Padding

    c = console()
    art = LOGO_WIDE if c.width >= max(len(l) for l in LOGO_WIDE.splitlines()) + 4 else LOGO_SMALL
    c.print()
    c.print(Padding(gradient(art), (0, 2)))
    c.print(f"  [dim]{_esc(subtitle)}[/]")
    c.print()


# --------------------------------------------------------------------------
# Building blocks
# --------------------------------------------------------------------------


def state(on: bool) -> str:
    return "[bold #4ade80]● ON[/]" if on else "[dim]○ OFF[/]"


def ok(message: str, indent: str = "") -> None:
    console().print(f"{indent}[bold #4ade80]✓[/] {message}")


def warn(message: str, indent: str = "") -> None:
    console().print(f"{indent}[bold yellow]![/] [yellow]{_esc(message)}[/]")


def _indented(renderable, spaces: int = 2):
    from rich.padding import Padding

    return Padding(renderable, (0, 0, 0, spaces))


def command(text: str) -> str:
    return f"[bold {ACCENT}]{_esc(text)}[/]"


def spinner(message: str):
    """A spinner on stderr while waiting on the network (terminal only)."""
    err = console(stderr=True)
    if not err.is_terminal:
        return nullcontext()
    return err.status(f"[dim]{_esc(message)}[/]", spinner="dots", spinner_style=ACCENT)


def ask(label: str, password: bool = False) -> str:
    return console(stderr=True).input(
        f"  [bold]{_esc(label)}[/] [{ACCENT}]›[/] ", password=password
    )


def _children(d: dict[str, Any], indent: str = "  ") -> None:
    kids = d.get("children", [])
    width = max((len(c["name"]) for c in kids), default=0)
    for n, c in enumerate(kids):
        branch = "└─" if n == len(kids) - 1 else "├─"
        name = _esc(c["name"].ljust(width))
        console().print(
            f"{indent}[dim]{branch} {c['index']}[/] {name}  {state(c['on'])}"
        )


def _details(d: dict[str, Any]) -> str:
    return f"[dim]{_esc(d['model'])} · {_esc(d['ip'])}[/]"


def _name(d: dict[str, Any]) -> str:
    name = f"[bold]{_esc(d['name'])}[/]"
    if "child" in d:
        name += f" [dim]›[/] [bold]{_esc(d['child'])}[/]"
    return name


# --------------------------------------------------------------------------
# Rendering command results (the same dicts that are printed as JSON)
# --------------------------------------------------------------------------


def render(result: dict[str, Any]) -> None:
    if not result.get("ok"):
        _error(result)
        return
    {
        "list": _list,
        "status": _status,
        "on": _switched,
        "off": _switched,
        "setup": _setup,
        "install": _install,
        "uninstall": _uninstall,
    }[result["action"]](result)


def error(message: str, hint: str | None = None) -> None:
    c = console()
    c.print(f"[bold red]✗[/] [red]{_esc(message)}[/]")
    if hint:
        c.print(f"  [dim]{hint}[/]")


def _error(result: dict[str, Any]) -> None:
    message = result.get("error", "unknown error")
    hint = None
    if message.startswith("authentication failed"):
        hint = "Fix the login with " + command("tapo-power setup") + "[dim]. Don't retry in a loop.[/]"
    elif "credentials missing" in message:
        message, hint = "No Tapo login saved yet.", "Run " + command("tapo-power setup") + "[dim] first.[/]"
    elif message.startswith("no device named"):
        hint = "See what's available with " + command("tapo-power list")
    error(message, hint)
    if "name" in result and "ip" in result:
        console().print(f"  {_name(result)}  {state(result['on'])}  {_details(result)}")
        _children(result, "    ")


def _status(d: dict[str, Any]) -> None:
    console().print(f"{_name(d)}  {state(d['on'])}  {_details(d)}")
    _children(d)


def _switched(d: dict[str, Any]) -> None:
    ok(f"{_name(d)} is now {state(d['on'])}  {_details(d)}")
    _children(d)


def _device_table(devices: list[dict[str, Any]], title: str):
    from rich import box
    from rich.table import Table

    table = Table(
        title=title,
        title_justify="left",
        title_style=f"bold {ACCENT}",
        box=box.ROUNDED,
        border_style="dim",
        header_style="bold",
        padding=(0, 1),
    )
    table.add_column("Name")
    table.add_column("Alias", style=ACCENT)
    table.add_column("State")
    table.add_column("Model", style="dim")
    table.add_column("IP", style="dim")
    for d in devices:
        table.add_row(
            _esc(d["name"]), _esc(d.get("alias", "")), state(d["on"]), _esc(d["model"]), _esc(d["ip"])
        )
        kids = d.get("children", [])
        for n, c in enumerate(kids):
            branch = "└─" if n == len(kids) - 1 else "├─"
            table.add_row(f"[dim]{branch} {c['index']}[/] {_esc(c['name'])}", "", state(c["on"]), "", "")
    return table


def _list(result: dict[str, Any]) -> None:
    c = console()
    devices = result["devices"]
    if devices:
        n = len(devices)
        c.print(_device_table(devices, f"{n} device{'s' if n != 1 else ''} on your network"))
    else:
        error(
            "No devices found.",
            "Check the plugs are powered and on the same network as this computer.",
        )
    for e in result.get("errors", []):
        warn(e)


def _setup(result: dict[str, Any]) -> None:
    # Indented to sit under the banner that setup shows first.
    c = console()
    c.print()
    ok(f"Login saved  [dim]{_esc(result['config'])}[/]", indent="  ")
    devices = result["devices"]
    if devices:
        c.print()
        c.print(_indented(_device_table(devices, "Your devices")))
        c.print()
        alias = devices[0]["alias"]
        c.print("  Try it:")
        c.print(f"    {command(f'tapo-power off {alias}')}")
        c.print(f"    {command(f'tapo-power on {alias}')}")
    if "warning" in result:
        warn(result["warning"], indent="  ")
    for e in result.get("errors", []):
        warn(e, indent="  ")
    c.print()


def _install(result: dict[str, Any]) -> None:
    ok(f"Installed  [dim]{_esc(result['path'])}[/]")
    if result.get("added_to_path"):
        ok("Added to your PATH")
    console().print(f"  [dim]Next:[/] {_esc(result['next'])}")


def _uninstall(result: dict[str, Any]) -> None:
    if result["removed"]:
        ok(f"Removed  [dim]{_esc(result['path'])}[/]")
    else:
        console().print(f"[dim]Nothing installed at {_esc(result['path'])}[/]")
    console().print("  [dim]Your saved login is kept.[/]")


# --------------------------------------------------------------------------
# Screens
# --------------------------------------------------------------------------


def welcome() -> None:
    """Shown when `tapo-power` is typed with no command in a terminal."""
    from rich.table import Table

    banner()
    table = Table.grid(padding=(0, 3))
    table.add_column(no_wrap=True)
    table.add_column(style="dim")
    for cmd, text in (
        ("tapo-power setup", "save your Tapo login and find your devices"),
        ("tapo-power list", "show every device on the network"),
        ("tapo-power status <name>", "is it on or off?"),
        ("tapo-power on <name>", "switch on"),
        ("tapo-power off <name>", "switch off"),
    ):
        table.add_row(command(cmd), text)
    c = console()
    c.print(_indented(table))
    c.print()
    c.print(f"  [dim]Add[/] --json [dim]for machine-readable output ·[/] --help [dim]for all options[/]")
    c.print()


def is_terminal() -> bool:
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False
