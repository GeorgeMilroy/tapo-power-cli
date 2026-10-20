"""Install the standalone executable so `tapo-power` works in any terminal.

Only relevant to the downloaded single-file program: pip/pipx installs put
the command on PATH already.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


class InstallError(Exception):
    pass


def is_frozen() -> bool:
    """True when running as the PyInstaller-built executable."""
    return bool(getattr(sys, "frozen", False))


def exe_name() -> str:
    return "tapo-power.exe" if sys.platform == "win32" else "tapo-power"


def default_install_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Programs" / "tapo-power"
    return Path.home() / ".local" / "bin"


def _same_dir(a: str, b: str) -> bool:
    def norm(p: str) -> str:
        return os.path.normcase(os.path.normpath(os.path.expandvars(p.strip())))

    return norm(a) == norm(b)


def on_path(directory: Path) -> bool:
    return any(
        _same_dir(entry, str(directory))
        for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry
    )


# --------------------------------------------------------------------------
# Windows user PATH (HKCU\Environment), the same setting as
# "Edit environment variables for your account".
# --------------------------------------------------------------------------


def _read_user_var(name: str) -> tuple[str, int]:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        try:
            value, kind = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            return "", winreg.REG_EXPAND_SZ
    return value or "", kind


def _write_user_var(name: str, value: str, kind: int) -> None:
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, name, 0, kind, value)
    _broadcast_environment_change()


def _broadcast_environment_change() -> None:
    """Tell Explorer the environment changed, so new terminals see it."""
    import ctypes
    from ctypes import wintypes

    result = wintypes.DWORD()
    ctypes.windll.user32.SendMessageTimeoutW(
        0xFFFF,  # HWND_BROADCAST
        0x001A,  # WM_SETTINGCHANGE
        0,
        "Environment",
        0x0002,  # SMTO_ABORTIFHUNG
        5000,
        ctypes.byref(result),
    )


def add_to_user_path(directory: Path, var: str = "Path") -> bool:
    """Append `directory` to the user's PATH. Returns False if already there."""
    value, kind = _read_user_var(var)
    entries = [e for e in value.split(";") if e]
    if any(_same_dir(e, str(directory)) for e in entries):
        return False
    # Keep the value's registry type: REG_EXPAND_SZ entries like
    # %USERPROFILE%\bin would break if rewritten as REG_SZ.
    _write_user_var(var, ";".join(entries + [str(directory)]), kind)
    return True


def remove_from_user_path(directory: Path, var: str = "Path") -> bool:
    value, kind = _read_user_var(var)
    entries = [e for e in value.split(";") if e]
    kept = [e for e in entries if not _same_dir(e, str(directory))]
    if len(kept) == len(entries):
        return False
    _write_user_var(var, ";".join(kept), kind)
    return True


# --------------------------------------------------------------------------
# install / uninstall
# --------------------------------------------------------------------------


def install(directory: Path | None = None) -> dict:
    if not is_frozen():
        raise InstallError(
            "'install' is for the downloaded tapo-power program; this copy was "
            "installed with Python (pip/pipx), so it is already on your PATH"
        )
    directory = directory or default_install_dir()
    source = Path(sys.executable).resolve()
    target = directory / exe_name()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if not (target.exists() and target.resolve() == source):
            shutil.copy2(source, target)
            if sys.platform != "win32":
                target.chmod(0o755)
    except PermissionError as exc:
        raise InstallError(
            f"cannot write {target} - close any other running tapo-power and "
            f"try again ({exc})"
        ) from exc
    except OSError as exc:
        raise InstallError(f"cannot install to {target}: {exc}") from exc

    result = {"ok": True, "action": "install", "path": str(target)}
    if sys.platform == "win32":
        result["added_to_path"] = add_to_user_path(directory)
        result["next"] = "open a new terminal and run: tapo-power setup"
    elif on_path(directory):
        result["added_to_path"] = False
        result["next"] = "run: tapo-power setup"
    else:
        result["added_to_path"] = False
        result["next"] = (
            f'{directory} is not on your PATH; add it, e.g. '
            f"echo 'export PATH=\"{directory}:$PATH\"' >> ~/.zshrc "
            "(or ~/.bashrc), open a new terminal, then run: tapo-power setup"
        )
    return result


def uninstall(directory: Path | None = None) -> dict:
    directory = directory or default_install_dir()
    target = directory / exe_name()
    result = {"ok": True, "action": "uninstall", "path": str(target)}
    if sys.platform == "win32":
        result["removed_from_path"] = remove_from_user_path(directory)

    if target.exists():
        running_self = is_frozen() and Path(sys.executable).resolve() == target.resolve()
        if running_self and sys.platform == "win32":
            # Windows can't delete a running exe: delete it once we've exited.
            subprocess.Popen(
                f'cmd /c ping -n 3 127.0.0.1 >nul & del /f /q "{target}" '
                f'& rmdir "{directory}"',
                creationflags=subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NO_WINDOW,
                close_fds=True,
            )
        else:
            target.unlink()
            if sys.platform == "win32":
                try:
                    directory.rmdir()  # only if now empty
                except OSError:
                    pass
        result["removed"] = True
    else:
        result["removed"] = False
    return result
