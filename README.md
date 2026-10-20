<p align="center">
  <img src="https://raw.githubusercontent.com/GeorgeMilroy/tapo-power-cli/main/docs/banner.jpg" alt="tapo-power: switch Tapo smart plugs from the command line" width="100%">
</p>

<h1 align="center">tapo-power</h1>

<p align="center">
  <b>Switch TP-Link Tapo smart plugs on and off from the command line, built so AI agents can use it too.</b>
</p>

<p align="center">
  <a href="https://github.com/GeorgeMilroy/tapo-power-cli/actions/workflows/ci.yml"><img src="https://github.com/GeorgeMilroy/tapo-power-cli/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue" alt="Python 3.11–3.14">
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey" alt="Windows, macOS, Linux">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT license"></a>
</p>

```console
$ tapo-power off "Tapo P100"
{"ok": true, "action": "off", "name": "Tapo P100", "ip": "192.168.1.15", "model": "P100", "on": false}
```

## Why

Tapo plugs are cheap and everywhere, but controlling them from a script or an AI agent usually means writing Python against an undocumented, changing protocol. `tapo-power` puts that behind four commands:

- **Talks to the plug directly over your Wi-Fi network.** Switching doesn't go through TP-Link's cloud, and it keeps working if the internet is down.
- **Built for agents.** Every command prints exactly one JSON object and exits with a meaningful code, so an LLM can call it and check the result without parsing prose.
- **Confirms what happened.** After `on` or `off` it reads the state back from the plug, so `"ok": true` means the plug really did switch (on a power strip, every socket did).
- **Works on current firmware.** It supports the newer TPAP login that recent Tapo firmware uses, which many older libraries can't handle.
- **One-step setup.** `tapo-power setup` asks for your login, checks it against your devices and saves it. You never edit a file by hand.

## Supported devices

| Type | Models |
|---|---|
| Smart plugs | P100, P105, P110, P110M, P115 |
| Power strips | P300, P304M, P306, P316M (whole strip or one socket) |
| Wall switches | S500, S505 and other models [python-kasa](https://github.com/python-kasa/python-kasa) supports (fallback) |

Tested on real hardware with a **P100** (UK, TPAP firmware). The other models are supported by the libraries underneath (see [How it works](#how-it-works)) but haven't been tested with this tool yet. Reports are welcome.

## Install

You need Python 3.11 or newer. The recommended way is [pipx](https://pipx.pypa.io), which installs `tapo-power` as a standalone command:

```bash
pipx install git+https://github.com/GeorgeMilroy/tapo-power-cli
```

<details>
<summary>Other ways to install</summary>

With [uv](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/GeorgeMilroy/tapo-power-cli
```

With plain pip in a virtual environment:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install git+https://github.com/GeorgeMilroy/tapo-power-cli
```

</details>

## Quick start

**1. Turn on third-party access in the Tapo app.** Go to **Me → Third-Party Services → Third-Party Compatibility**. Recent firmware rejects logins from anything other than the Tapo app until you do this.

**2. Run setup** and enter the email and password you use for the Tapo app:

```console
$ tapo-power setup --text
Tapo app account email: you@example.com
Tapo app password (hidden):
saved login to C:\Users\you\AppData\Roaming\tapo-power\config.toml
  tapo-p100: Tapo P100 (P100 @ 192.168.1.15): ON
try: tapo-power status tapo-p100
```

Setup logs in to each device it finds. If every device that answers rejects the login, it reports a wrong password and saves nothing. Devices on someone else's Tapo account are listed as warnings and skipped. If no device answers at all (for example on a guest network), setup saves the login anyway with `"verified": false` and a warning.

Setup also gives each device a short alias (`tapo-p100` above) that's quick to type and quick to connect to. Devices with the same name get `-2`, `-3` and so on. Run setup again any time to pick up new devices or changed IP addresses.

If `TAPO_USERNAME` and `TAPO_PASSWORD` are set, setup uses them instead of asking. For scripted installs you can pass `--username` and `--password`.

**3. Switch things:**

```bash
tapo-power off tapo-p100
tapo-power on tapo-p100
tapo-power status tapo-p100
```

## Usage

```
tapo-power setup                          save your login and find your devices
tapo-power list                           show every device on the network
tapo-power status <target>                is it on or off?
tapo-power off    <target> [--child N]    switch off
tapo-power on     <target> [--child N]    switch on
```

**`<target>`** can be any of:

| You type | Example | Speed |
|---|---|---|
| an alias from your config | `tapo-p100` | fast, connects directly |
| an IP address or hostname | `192.168.1.15`, `plug.lan` | fast, connects directly |
| the name from the Tapo app (any case) | `"Tapo P100"` | slower, searches the network first |

If two devices share a name, a name lookup refuses to guess: it lists the matches and exits with code 2. Use the IP address or the alias instead.

**Power strips:** without `--child`, `on`/`off` switch every socket. Add `--child 2` (1-based socket number) or `--child "Desk Lamp"` (socket name) to switch just one. A socket's name also works directly as the target.

**Options** (before or after the command):

| Option | Meaning |
|---|---|
| `--text` | human-readable output instead of JSON |
| `--timeout SECONDS` | network timeout, 1–60, default 10 |
| `--username`, `--password` | use this login instead of the saved one |
| `--no-discover` | `setup` only: save the login without contacting any device |

### Output

Every command prints a single JSON object on stdout, including when you get the arguments wrong. Only `--help` and `--version` print plain text.

```json
{"ok": true, "action": "status", "name": "Tapo P100", "ip": "192.168.1.15", "model": "P100", "on": true}
```

Power strips add a `children` list (`{"index": 1, "name": "...", "on": true}` per socket), and `child` names the socket a command acted on. A strip's top-level `"on"` is `true` when any socket is on. `list` returns `{"ok": true, "action": "list", "devices": [...]}`, plus an `errors` list for devices it couldn't read (for example a plug on a different Tapo account). Failures look like this:

```json
{"ok": false, "error": "no device named 'kitchen' found; known names: Tapo P100, tapo-p100"}
```

| Exit code | Meaning |
|---|---|
| `0` | success, state confirmed |
| `1` | device or network problem (plug unplugged, wrong IP, timeout) |
| `2` | usage or config problem (unknown name, missing login) |
| `3` | login rejected (wrong email or password) |

## Using it with an AI agent

`tapo-power` is a plain command, so any agent that can run shell commands can use it: Claude Code, Codex, Cursor, Open Interpreter, or your own tool-calling loop. Install it, run `tapo-power setup` once yourself, then give the agent instructions like these, for example in `CLAUDE.md`, `AGENTS.md` or a system prompt:

```markdown
## Smart plugs
Control the Tapo smart plugs with the `tapo-power` command. It prints one JSON object.
- `tapo-power off <alias>` / `tapo-power on <alias>`: switch a device
- `tapo-power status <alias>`: check a device
- `tapo-power list`: see which devices exist (only if you need to)
Devices: tapo-p100 = desk lamp, heater = office heater   <- replace with yours
"ok": true means the command worked, and "on" is the confirmed state.
Exit code 3 means the login was rejected. Don't retry it; tell the user,
because repeated failed logins lock the plug.
```

If your framework needs an explicit tool definition, wrap the command in a single function:

```json
{
  "name": "tapo_power",
  "description": "Switch a Tapo smart plug on or off, or check its state. Returns JSON with 'ok' and the confirmed 'on' state.",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["on", "off", "status", "list"]},
      "target": {"type": "string", "description": "Device alias, name or IP. Not used for 'list'."}
    },
    "required": ["action"]
  }
}
```

The agent never needs to see your password. It's read from the config file, which only you set up.

## Credentials and security

- **What login is used?** The email and password of your TP-Link / Tapo app account. Tapo devices check this account locally to authorise commands; switching itself never goes through TP-Link's cloud.
- **Where is it stored?** `tapo-power setup` writes it to:
  - Windows: `%APPDATA%\tapo-power\config.toml`
  - macOS/Linux: `$XDG_CONFIG_HOME/tapo-power/config.toml`, which is normally `~/.config/tapo-power/config.toml` (created with `600` permissions, so only you can read it)

  The password is stored in plain text, like most CLI tools' config files, so treat the file like a password.
- **Prefer not to store it?** Set the `TAPO_USERNAME` and `TAPO_PASSWORD` environment variables instead, for example from your password manager's CLI. Order of precedence: command-line flags, then environment variables, then the config file.
- **Different config location:** set `TAPO_POWER_CONFIG=/path/to/config.toml`.
- **Lockout protection:** Tapo plugs lock themselves for a while after several failed logins. `tapo-power` never retries a rejected login, and it tells agents not to retry either.
- **Shared networks:** `list`, `setup` and lookups by name try your login on every Tapo plug that answers. On a shared network (flatmates, an office), a plug on someone else's account rejects it, which counts as a failed login on *their* plug. Switching by alias or IP only contacts that one device, so prefer those in scripts and agent instructions.

The config file is plain TOML if you want to edit it ([example](config.example.toml)):

```toml
username = "you@example.com"
password = "your-tapo-password"

[devices]
tapo-p100 = "192.168.1.15"
heater    = "192.168.1.41"
```

## Troubleshooting

<details>
<summary><b>Exit code 3 / "authentication failed"</b></summary>

- Check you're using the **Tapo app** email and password, and remember the password is case-sensitive.
- Turn on **Third-Party Compatibility** in the Tapo app (Me → Third-Party Services).
- If you've had several failures, wait a few minutes before trying again; the plug may have locked itself.
- If you changed your password recently, power-cycle the plug so it picks up the new one.

</details>

<details>
<summary><b><code>list</code> finds nothing / "no Tapo device responded"</b></summary>

- Your computer and the plug must be on the same network (guest Wi-Fi networks and VLANs often block this).
- Discovery uses UDP port 20002. Allow Python through your firewall if it asks.
- Use the IP address directly: you can find it in the Tapo app (device → ⚙ → Device Info) or on your router.

</details>

<details>
<summary><b>The IP address keeps changing</b></summary>

Reserve a fixed IP for each plug in your router's DHCP settings, then run `tapo-power setup` again to refresh the aliases.

</details>

## How it works

`tapo-power` is a thin layer over two open-source libraries:

- [**tapo**](https://github.com/mihai-dinculescu/tapo) (Rust, with Python bindings) handles plugs and power strips, including the newer TPAP, KLAP and passthrough login protocols.
- [**python-kasa**](https://github.com/python-kasa/python-kasa) is the fallback for devices `tapo` doesn't model, such as S500/S505 wall switches.

Devices are found with Tapo's UDP discovery broadcast. Each command opens a short-lived local session, does its job and exits; nothing runs in the background.

## Development

```bash
git clone https://github.com/GeorgeMilroy/tapo-power-cli
cd tapo-power-cli
python -m venv .venv
.venv/bin/pip install -e ".[dev]"      # Windows: .venv\Scripts\pip ...
.venv/bin/python -m pytest
```

The tests use simulated devices, so they don't need any hardware. Issues and pull requests are welcome, especially reports from models other than the P100.

## Disclaimer

This is an independent project and is not affiliated with or endorsed by TP-Link. "Tapo" and "TP-Link" are trademarks of their respective owner. Don't use this tool to switch anything where an unexpected power cut would be unsafe.

## License

[MIT](LICENSE)
