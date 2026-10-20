import json

import pytest

from tapo_power import backends, cli
from tapo_power.backends import AuthError, BackendError, Socket, Switch


class FakeSwitch(Switch):
    def __init__(self, name, ip="192.168.1.40", model="P100", on=True, children=()):
        self.name, self.ip, self.model, self._on = name, ip, model, on
        self.children = [Socket(i, n, True) for i, n in enumerate(children, 1)]
        self.closed = False
        self.fail_with = None
        self.stuck = set()  # socket indexes that ignore commands

    @property
    def on(self):
        return any(s.on for s in self.children) if self.children else self._on

    async def refresh(self):
        pass

    async def set_power(self, on, socket=None):
        if self.fail_with:
            raise self.fail_with
        if self.children:
            for s in [socket] if socket else self.children:
                if s.index not in self.stuck:
                    s.on = on
        else:
            self._on = on

    async def close(self):
        self.closed = True


@pytest.fixture
def env(monkeypatch, tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[devices]\ndesk = "192.168.1.40"\n')
    monkeypatch.setenv("TAPO_POWER_CONFIG", str(cfg))
    monkeypatch.setenv("TAPO_USERNAME", "u@example.com")
    monkeypatch.setenv("TAPO_PASSWORD", "pw")
    devices = FakeLan()

    async def connect(host, username, password, timeout):
        if host not in devices:
            raise BackendError(f"no Tapo device responded at {host}")
        return devices[host]

    async def discover(username, password, timeout):
        return backends.Discovery(
            switches=list(devices.values()), auth_errors=list(devices.auth_errors)
        )

    async def resolve_ip(host):
        if host in devices:
            return host
        raise BackendError(f"cannot resolve host '{host}'")

    monkeypatch.setattr(backends, "connect", connect)
    monkeypatch.setattr(backends, "discover", discover)
    monkeypatch.setattr(backends, "resolve_ip", resolve_ip)
    return devices


class FakeLan(dict):
    """IP -> FakeSwitch, plus logins rejected by devices on other accounts."""

    def __init__(self):
        super().__init__()
        self.auth_errors = []


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_off_by_config_alias(env, capsys):
    dev = env["192.168.1.40"] = FakeSwitch("Desk Lamp")
    code, out = run(capsys, "off", "desk")
    assert code == 0
    assert out == {
        "ok": True, "action": "off", "name": "Desk Lamp",
        "ip": "192.168.1.40", "model": "P100", "on": False,
    }
    assert dev.closed


def test_on_by_ip_is_idempotent(env, capsys):
    env["10.0.0.5"] = FakeSwitch("Heater", ip="10.0.0.5", on=True)
    code, out = run(capsys, "on", "10.0.0.5")
    assert code == 0 and out["on"] is True


def test_off_by_device_name_via_discovery(env, capsys):
    other = env["10.0.0.6"] = FakeSwitch("Fan", ip="10.0.0.6")
    env["10.0.0.5"] = FakeSwitch("Tapo P100", ip="10.0.0.5")
    code, out = run(capsys, "off", "tapo p100")
    assert code == 0 and out["name"] == "Tapo P100" and out["on"] is False
    assert other.closed


def test_power_strip_child_by_index_and_name(env, capsys):
    strip = env["10.0.0.7"] = FakeSwitch(
        "Strip", ip="10.0.0.7", model="P300", children=["Socket A", "Socket B"]
    )
    a, b = strip.children
    code, out = run(capsys, "off", "10.0.0.7", "--child", "2")
    assert code == 0 and out["child"] == "Socket B" and out["on"] is False
    assert a.on and not b.on
    code, out = run(capsys, "off", "socket a")  # socket name resolved via discovery
    assert code == 0 and out["child"] == "Socket A" and not a.on


def test_whole_strip_off(env, capsys):
    env["10.0.0.7"] = FakeSwitch("Strip", ip="10.0.0.7", children=["A", "B"])
    code, out = run(capsys, "off", "10.0.0.7")
    assert code == 0 and out["on"] is False
    assert [c["on"] for c in out["children"]] == [False, False]


def test_unknown_child_is_usage_error(env, capsys):
    env["10.0.0.7"] = FakeSwitch("Strip", ip="10.0.0.7", children=["A"])
    code, out = run(capsys, "off", "10.0.0.7", "--child", "9")
    assert code == 2 and not out["ok"] and "available" in out["error"]


def test_status_and_list(env, capsys):
    env["192.168.1.40"] = FakeSwitch("Desk Lamp", on=False)
    code, out = run(capsys, "status", "desk")
    assert code == 0 and out["on"] is False
    code, out = run(capsys, "list")
    assert code == 0 and out["devices"][0]["alias"] == "desk"


def test_unknown_name(env, capsys):
    env["10.0.0.5"] = FakeSwitch("Heater", ip="10.0.0.5")
    code, out = run(capsys, "off", "nothing")
    assert code == 2 and "no device named 'nothing'" in out["error"]
    assert "Heater" in out["error"]


def test_no_response_at_host(env, capsys):
    code, out = run(capsys, "off", "10.9.9.9")
    assert code == 1 and "no Tapo" in out["error"]


def test_missing_credentials(env, capsys, monkeypatch):
    monkeypatch.delenv("TAPO_PASSWORD")
    monkeypatch.delenv("TAPO_USERNAME")
    code, out = run(capsys, "off", "desk")
    assert code == 2 and "credentials" in out["error"]


@pytest.mark.parametrize(
    "exc, expected", [(AuthError("bad"), 3), (BackendError("boom"), 1)]
)
def test_error_mapping(env, capsys, exc, expected):
    dev = env["192.168.1.40"] = FakeSwitch("Desk Lamp")
    dev.fail_with = exc
    code, out = run(capsys, "off", "desk")
    assert code == expected and not out["ok"]
    assert dev.closed


def test_global_flag_before_subcommand(env, capsys):
    env["192.168.1.40"] = FakeSwitch("Desk Lamp")
    assert cli.main(["--text", "off", "desk"]) == 0
    assert capsys.readouterr().out.strip() == "Desk Lamp (P100 @ 192.168.1.40): OFF"


def test_tapo_error_classification():
    msg = 'Tapo(Unauthorized { kind: "TPAP_CREDENTIALS", description: "..." })'
    assert isinstance(backends._tapo_error(Exception(msg)), AuthError)
    assert not isinstance(backends._tapo_error(Exception("timeout")), AuthError)


def test_setup_saves_verified_credentials_and_aliases(env, capsys, monkeypatch, tmp_path):
    from tapo_power.config import load_config

    path = tmp_path / "new" / "config.toml"
    monkeypatch.setenv("TAPO_POWER_CONFIG", str(path))
    env["192.168.1.15"] = FakeSwitch("Tapo P100", ip="192.168.1.15")
    code, out = run(capsys, "setup", "--username", "me@example.com", "--password", 'p"w')
    assert code == 0 and out["verified"] is True
    assert out["devices"][0]["alias"] == "tapo-p100"
    cfg = load_config(path)
    assert (cfg.username, cfg.password) == ("me@example.com", 'p"w')
    assert cfg.devices == {"tapo-p100": "192.168.1.15"}
    # The saved file is enough on its own for later commands.
    monkeypatch.delenv("TAPO_USERNAME")
    monkeypatch.delenv("TAPO_PASSWORD")
    code, out = run(capsys, "off", "tapo-p100")
    assert code == 0 and out["on"] is False


def test_setup_bad_password_saves_nothing(env, capsys, monkeypatch, tmp_path):
    path = tmp_path / "fresh.toml"
    monkeypatch.setenv("TAPO_POWER_CONFIG", str(path))

    async def discover(username, password, timeout):
        raise AuthError("Unauthorized")

    monkeypatch.setattr(backends, "discover", discover)
    code, out = run(capsys, "setup", "--username", "a", "--password", "b")
    assert code == 3 and not path.exists()


def test_setup_keeps_existing_aliases_and_warns_when_nothing_found(env, capsys):
    code, out = run(capsys, "setup", "--username", "a", "--password", "b")
    assert code == 0 and out["verified"] is False and "warning" in out
    from tapo_power.config import load_config

    assert load_config().devices == {"desk": "192.168.1.40"}


def test_setup_without_terminal_fails_cleanly(env, capsys, monkeypatch):
    monkeypatch.delenv("TAPO_USERNAME")
    monkeypatch.delenv("TAPO_PASSWORD")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    code, out = run(capsys, "setup")
    assert code == 2 and "--username" in out["error"]


# --- Regression tests from the pre-publish review ---------------------------


def test_neighbours_plug_on_other_account_does_not_break_list_or_names(env, capsys):
    env["192.168.1.15"] = FakeSwitch("Tapo P100", ip="192.168.1.15")
    env.auth_errors.append("Unauthorized")
    code, out = run(capsys, "list")
    assert code == 0 and len(out["devices"]) == 1
    assert out["errors"] == ["authentication failed: Unauthorized"]
    code, out = run(capsys, "off", "Tapo P100")
    assert code == 0 and out["on"] is False


def test_every_device_rejecting_login_is_auth_error(env, capsys):
    env.auth_errors.append("Unauthorized")
    assert run(capsys, "list")[0] == 3
    assert run(capsys, "off", "some name")[0] == 3


def test_setup_with_some_devices_rejecting_still_saves(env, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("TAPO_POWER_CONFIG", str(tmp_path / "fresh.toml"))
    env["192.168.1.15"] = FakeSwitch("Tapo P100", ip="192.168.1.15")
    env.auth_errors.append("Unauthorized")
    code, out = run(capsys, "setup", "--username", "a", "--password", "b")
    assert code == 0 and out["verified"] is True and out["errors"]


def test_setup_when_every_device_rejects_saves_nothing(env, capsys, monkeypatch, tmp_path):
    path = tmp_path / "fresh.toml"
    monkeypatch.setenv("TAPO_POWER_CONFIG", str(path))
    env.auth_errors.append("Unauthorized")
    code, out = run(capsys, "setup", "--username", "a", "--password", "b")
    assert code == 3 and not path.exists()


def test_duplicate_names_get_unique_aliases_and_name_lookup_is_ambiguous(
    env, capsys, monkeypatch, tmp_path
):
    from tapo_power.config import load_config

    monkeypatch.setenv("TAPO_POWER_CONFIG", str(tmp_path / "fresh.toml"))
    env["10.0.0.1"] = FakeSwitch("Tapo P100", ip="10.0.0.1")
    env["10.0.0.2"] = FakeSwitch("Tapo P100", ip="10.0.0.2")
    code, out = run(capsys, "setup", "--username", "a", "--password", "b")
    assert [d["alias"] for d in out["devices"]] == ["tapo-p100", "tapo-p100-2"]
    assert load_config().devices == {"tapo-p100": "10.0.0.1", "tapo-p100-2": "10.0.0.2"}
    code, out = run(capsys, "off", "Tapo P100")
    assert code == 2 and "matches 2 devices" in out["error"]
    assert env["10.0.0.1"].on and env["10.0.0.2"].on  # nothing was switched


def test_setup_moves_alias_when_device_ip_changed(env, capsys, monkeypatch, tmp_path):
    from tapo_power.config import load_config

    cfg = tmp_path / "moved.toml"
    cfg.write_text('[devices]\n"tapo-p100" = "10.0.0.9"\n')
    monkeypatch.setenv("TAPO_POWER_CONFIG", str(cfg))
    env["10.0.0.1"] = FakeSwitch("Tapo P100", ip="10.0.0.1")
    run(capsys, "setup", "--username", "a", "--password", "b")
    assert load_config().devices == {"tapo-p100": "10.0.0.1"}


def test_names_with_dots_fall_back_to_discovery(env, capsys):
    env["10.0.0.3"] = FakeSwitch("Mr.Coffee", ip="10.0.0.3")
    env["10.0.0.4"] = FakeSwitch("Lamp 2.0", ip="10.0.0.4")
    assert run(capsys, "off", "Mr.Coffee")[1]["name"] == "Mr.Coffee"
    assert run(capsys, "off", "lamp 2.0")[1]["name"] == "Lamp 2.0"


def test_whole_strip_on_requires_every_socket(env, capsys):
    strip = env["10.0.0.7"] = FakeSwitch("Strip", ip="10.0.0.7", children=["A", "B"])
    run(capsys, "off", "10.0.0.7")
    strip.stuck = {2}
    code, out = run(capsys, "on", "10.0.0.7")
    assert code == 1 and out["ok"] is False and out["on"] is True


def test_usage_errors_are_json(capsys):
    for argv in (["off"], ["frobnicate"], ["--timeout", "0", "list"], ["--timeout", "x", "list"]):
        with pytest.raises(SystemExit) as exc:
            cli.main(argv)
        assert exc.value.code == 2
        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is False and out["error"]


def test_emoji_credentials_round_trip(tmp_path):
    from tapo_power.config import Config, load_config, save_config

    path = tmp_path / "c.toml"
    pw = 'p"\\w\U0001F600\u00e9\x7f\n'
    save_config(Config("u\U0001F600@example.com", pw, {"Désk": "1.2.3.4"}), path)
    cfg = load_config(path)
    assert cfg.password == pw and cfg.username == "u\U0001F600@example.com"


def test_setup_repairs_unreadable_config(env, capsys, monkeypatch, tmp_path):
    cfg = tmp_path / "broken.toml"
    cfg.write_text("this is = = not toml")
    monkeypatch.setenv("TAPO_POWER_CONFIG", str(cfg))
    assert run(capsys, "status", "x")[0] == 2
    code, out = run(capsys, "setup", "--username", "a", "--password", "b", "--no-discover")
    assert code == 0 and "username" in cfg.read_text()


def test_overall_timeout_gives_clear_json(env, capsys, monkeypatch):
    import asyncio

    async def hang(*a, **k):
        await asyncio.sleep(10)

    monkeypatch.setattr(backends, "connect", hang)

    real_wait_for = asyncio.wait_for

    async def fast_wait_for(coro, timeout):
        return await real_wait_for(coro, 0.01)

    monkeypatch.setattr(cli.asyncio, "wait_for", fast_wait_for)
    code, out = run(capsys, "off", "desk")
    assert code == 1 and "timed out after 40s" in out["error"]


# --- backends: discovery routing (fake tapo library) -------------------------


class _Plug:
    def __init__(self, ip, name):
        self.ip, self.model, self.nickname = ip, "P100", name
        self.device_info = type("Info", (), {"nickname": name, "device_on": True})()
        self.handler = self

    async def get_device_info(self):
        return self.device_info


class _Other:
    ip = "10.0.0.50"


class _Light:
    ip = "10.0.0.60"


class _Maybe:
    def __init__(self, value):
        self.value = value

    def get(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def _fake_tapo(monkeypatch, items):
    class Client:
        def __init__(self, *a, **k):
            pass

        async def discover_devices(self, target, timeout_s):
            async def gen():
                for item in items:
                    yield _Maybe(item)

            return gen()

    monkeypatch.setattr(backends.tapo, "ApiClient", Client, raising=False)
    monkeypatch.setattr(backends, "_PLUG_TYPES", (_Plug,))
    monkeypatch.setattr(backends, "_STRIP_TYPES", ())
    monkeypatch.setattr(backends, "_UNMODELLED_TYPE", _Other)


def test_discovery_routes_only_unmodelled_devices_to_kasa(monkeypatch):
    import asyncio

    tried = []

    async def kasa_connect(ip, *a):
        tried.append(ip)
        return None

    _fake_tapo(
        monkeypatch,
        [_Plug("10.0.0.1", "Mine"), _Other(), _Light(), Exception("Tapo(Unauthorized {})")],
    )
    monkeypatch.setattr(backends, "_kasa_connect", kasa_connect)
    found = asyncio.run(backends.discover("u", "p", 5))
    assert [s.name for s in found.switches] == ["Mine"]
    assert tried == ["10.0.0.50"]  # the light was not handed to kasa
    assert len(found.auth_errors) == 1


def test_connect_raises_auth_error_for_single_host(monkeypatch):
    import asyncio

    _fake_tapo(monkeypatch, [Exception("Tapo(Unauthorized {})")])
    with pytest.raises(AuthError):
        asyncio.run(backends.connect("10.0.0.1", "u", "p", 5))


def test_resolve_ip_accepts_hostnames():
    import asyncio

    assert asyncio.run(backends.resolve_ip("localhost")) == "127.0.0.1"
    assert asyncio.run(backends.resolve_ip("10.1.2.3")) == "10.1.2.3"
