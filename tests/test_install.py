import json
import sys

import pytest

from tapo_power import cli
from tapo_power import install as installer


@pytest.fixture
def frozen_exe(monkeypatch, tmp_path):
    """Pretend we're the PyInstaller-built program."""
    exe = tmp_path / "download" / installer.exe_name()
    exe.parent.mkdir()
    exe.write_bytes(b"fake exe")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    calls = []
    monkeypatch.setattr(installer, "add_to_user_path", lambda d: calls.append(("add", d)) or True)
    monkeypatch.setattr(
        installer, "remove_from_user_path", lambda d: calls.append(("remove", d)) or True
    )
    return calls


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_install_refused_for_pip_installs(capsys, tmp_path):
    code, out = run(capsys, "install", "--dir", str(tmp_path))
    assert code == 2 and "already on your PATH" in out["error"]


def test_install_copies_and_uninstall_removes(frozen_exe, capsys, tmp_path):
    target_dir = tmp_path / "Programs" / "tapo-power"
    code, out = run(capsys, "install", "--dir", str(target_dir))
    target = target_dir / installer.exe_name()
    assert code == 0 and out["path"] == str(target)
    assert target.read_bytes() == b"fake exe"
    if sys.platform == "win32":
        assert frozen_exe == [("add", target_dir)] and out["added_to_path"]

    # Installing again over the same copy is fine.
    assert run(capsys, "install", "--dir", str(target_dir))[0] == 0

    code, out = run(capsys, "uninstall", "--dir", str(target_dir))
    assert code == 0 and out["removed"] is True and not target.exists()
    if sys.platform == "win32":
        assert ("remove", target_dir) in frozen_exe


def test_uninstall_when_nothing_installed(frozen_exe, capsys, tmp_path):
    code, out = run(capsys, "uninstall", "--dir", str(tmp_path / "nope"))
    assert code == 0 and out["removed"] is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
def test_user_path_editing_preserves_entries_and_type():
    import winreg

    var = "TAPO_POWER_TEST_PATH"
    original = r"%USERPROFILE%\bin;C:\Other"
    installer._write_user_var(var, original, winreg.REG_EXPAND_SZ)
    try:
        d = installer.Path(r"C:\Users\x\AppData\Local\Programs\tapo-power")
        assert installer.add_to_user_path(d, var) is True
        assert installer.add_to_user_path(d, var) is False  # no duplicates
        value, kind = installer._read_user_var(var)
        assert value == original + ";" + str(d) and kind == winreg.REG_EXPAND_SZ
        assert installer.remove_from_user_path(d, var) is True
        assert installer._read_user_var(var) == (original, winreg.REG_EXPAND_SZ)
        assert installer.remove_from_user_path(d, var) is False
    finally:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, var)


def test_not_double_click_when_run_from_tests():
    # pytest runs inside a terminal/pipe, never as a double-click.
    assert cli.launched_by_double_click() is False
