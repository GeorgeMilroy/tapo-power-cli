"""Credentials and device-alias configuration."""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    """Raised when the config file is unreadable or invalid."""


@dataclass
class Config:
    username: str | None = None
    password: str | None = None
    devices: dict[str, str] = field(default_factory=dict)
    path: Path | None = None


def default_config_path() -> Path:
    if override := os.environ.get("TAPO_POWER_CONFIG"):
        return Path(override)
    if sys.platform == "win32" and (appdata := os.environ.get("APPDATA")):
        return Path(appdata) / "tapo-power" / "config.toml"
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "tapo-power" / "config.toml"


def load_config(path: Path | None = None) -> Config:
    """Load the config file; a missing file yields an empty config."""
    path = path or default_config_path()
    if not path.is_file():
        return Config(path=path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc

    devices = data.get("devices", {})
    if not isinstance(devices, dict) or not all(
        isinstance(v, str) for v in devices.values()
    ):
        raise ConfigError(f"[devices] in {path} must map names to host strings")

    return Config(
        username=data.get("username"),
        password=data.get("password"),
        devices={k.lower(): v for k, v in devices.items()},
        path=path,
    )


def alias_for(name: str) -> str:
    """Turn a device name like "Tapo P100" into a config alias like "tapo-p100"."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "device"


def _toml_str(value: str) -> str:
    # JSON's escapes for quotes, backslashes and control characters are valid
    # TOML. Non-ASCII is written literally (the file is UTF-8): JSON's
    # surrogate-pair escapes for emoji would be invalid TOML. DEL isn't
    # escaped by JSON but must be in TOML.
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def save_config(cfg: Config, path: Path | None = None) -> Path:
    """Write the config file, readable only by the current user where possible."""
    path = path or cfg.path or default_config_path()
    lines = [
        "# Written by `tapo-power setup`. Your Tapo app account is used for",
        "# local device login; keep this file private.",
    ]
    if cfg.username:
        lines.append(f"username = {_toml_str(cfg.username)}")
    if cfg.password:
        lines.append(f"password = {_toml_str(cfg.password)}")
    lines += ["", "# Friendly names -> IP address or hostname.", "[devices]"]
    lines += [
        f"{_toml_str(name)} = {_toml_str(host)}"
        for name, host in sorted(cfg.devices.items())
    ]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Owner-only permissions on POSIX; on Windows the per-user %APPDATA%
        # folder is already private.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines) + "\n")
        if sys.platform != "win32":
            os.chmod(path, 0o600)
    except OSError as exc:
        raise ConfigError(f"cannot write config {path}: {exc}") from exc
    cfg.path = path
    return path
