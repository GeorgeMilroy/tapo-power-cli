import json

import pytest

from tapo_power import cli, ui

PLUG = {"name": "Desk [Lamp]", "ip": "192.168.1.15", "model": "P100", "on": False}
STRIP = {
    "name": "Strip",
    "ip": "10.0.0.7",
    "model": "P300",
    "on": True,
    "children": [
        {"index": 1, "name": "Kettle", "on": True},
        {"index": 2, "name": "Toaster", "on": False},
    ],
}


@pytest.fixture(autouse=True)
def fresh_consoles():
    ui.reset()
    yield
    ui.reset()


@pytest.mark.parametrize(
    "result, expected",
    [
        ({"ok": True, "action": "status", **PLUG}, ["Desk [Lamp]", "OFF", "P100"]),
        ({"ok": True, "action": "off", **PLUG}, ["Desk [Lamp] is now", "OFF"]),
        ({"ok": True, "action": "on", **STRIP}, ["Strip is now", "Kettle", "Toaster"]),
        (
            {"ok": True, "action": "list", "devices": [{**PLUG, "alias": "desk"}, STRIP],
             "errors": ["10.0.0.9: authentication failed"]},
            ["2 devices on your network", "desk", "Kettle", "authentication failed"],
        ),
        ({"ok": True, "action": "list", "devices": []}, ["No devices found"]),
        (
            {"ok": True, "action": "setup", "config": "C:/cfg.toml", "verified": True,
             "devices": [{**PLUG, "alias": "desk-lamp"}]},
            ["Login saved", "Your devices", "tapo-power off desk-lamp"],
        ),
        (
            {"ok": True, "action": "install", "path": "C:/x/tapo-power.exe",
             "added_to_path": True, "next": "open a new terminal"},
            ["Installed", "Added to your PATH", "open a new terminal"],
        ),
        ({"ok": True, "action": "uninstall", "path": "C:/x", "removed": True}, ["Removed", "login is kept"]),
        ({"ok": False, "error": "authentication failed - bad"}, ["authentication failed", "tapo-power setup"]),
        ({"ok": False, "error": "Tapo credentials missing: run"}, ["No Tapo login saved", "tapo-power setup"]),
        ({"ok": False, "action": "on", "error": "device did not report on", **PLUG}, ["did not report", "Desk [Lamp]"]),
    ],
)
def test_render_every_result(capsys, result, expected):
    ui.render(result)
    out = capsys.readouterr().out
    for text in expected:
        assert text in out, (text, out)
    assert "\x1b[" not in out  # no colour codes when not a terminal


def test_gradient_spreads_colours_across_the_width():
    text = ui.gradient("abcdefghij")
    colours = {str(span.style).split()[-1] for span in text.spans}
    assert len(colours) >= 5
    assert ui.blend(ui.GRADIENT, 0) == ui.GRADIENT[0]
    assert ui.blend(ui.GRADIENT, 1) == ui.GRADIENT[-1]


def test_banner_and_welcome_render(capsys):
    ui.banner()
    ui.welcome()
    out = capsys.readouterr().out
    assert "tapo-power setup" in out and "switch Tapo smart plugs" in out


def test_json_when_not_a_terminal_and_flags_override(monkeypatch):
    monkeypatch.setattr(ui, "is_terminal", lambda: False)
    assert cli.wants_human([]) is False
    assert cli.wants_human([], text=True) is True
    monkeypatch.setattr(ui, "is_terminal", lambda: True)
    assert cli.wants_human([]) is True
    assert cli.wants_human([], as_json=True) is False


def test_usage_error_is_friendly_in_a_terminal(monkeypatch, capsys):
    monkeypatch.setattr(ui, "is_terminal", lambda: True)
    monkeypatch.setattr("sys.argv", ["tapo-power", "off"])
    with pytest.raises(SystemExit):
        cli.main(["off"])
    out = capsys.readouterr().out
    assert "required" in out and not out.lstrip().startswith("{")


def test_usage_error_is_json_with_json_flag(monkeypatch, capsys):
    monkeypatch.setattr(ui, "is_terminal", lambda: True)
    monkeypatch.setattr("sys.argv", ["tapo-power", "off", "--json"])
    with pytest.raises(SystemExit):
        cli.main(["off", "--json"])
    assert json.loads(capsys.readouterr().out)["ok"] is False
