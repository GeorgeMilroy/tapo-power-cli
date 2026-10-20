"""Device access, hiding which library talks to which device.

The `tapo` library is used first: it supports every Tapo plug and power
strip, including firmware that uses the newer TPAP login scheme. Devices it
doesn't model (e.g. S500/S505 wall switches) fall back to `python-kasa`.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import kasa
import tapo


class BackendError(Exception):
    """Device or network failure."""


class AuthError(BackendError):
    """The device rejected the credentials."""


@dataclass
class Socket:
    """One outlet of a power strip."""

    index: int  # 1-based
    name: str
    on: bool
    device_id: str = ""


class Switch(ABC):
    name: str
    ip: str
    model: str
    on: bool
    children: list[Socket]

    @abstractmethod
    async def refresh(self) -> None: ...

    @abstractmethod
    async def set_power(self, on: bool, socket: Socket | None = None) -> None:
        """Switch the whole device, or just one socket of a power strip."""

    async def close(self) -> None:
        pass


# --------------------------------------------------------------------------
# tapo backend
# --------------------------------------------------------------------------

_PLUG_TYPES = (tapo.DiscoveryResult.Plug, tapo.DiscoveryResult.PlugEnergyMonitoring)
_STRIP_TYPES = (
    tapo.DiscoveryResult.PowerStrip,
    tapo.DiscoveryResult.PowerStripEnergyMonitoring,
)
# Devices tapo has no model for (e.g. S500/S505); tried with python-kasa.
_UNMODELLED_TYPE = tapo.DiscoveryResult.Other


def _tapo_error(exc: Exception) -> BackendError:
    text = str(exc)
    if "Unauthorized" in text or "InvalidCredentials" in text:
        return AuthError(text)
    return BackendError(text)


class TapoSwitch(Switch):
    def __init__(self, result):
        self._handler = result.handler
        self._strip = isinstance(result, _STRIP_TYPES)
        self.ip = result.ip
        self.model = result.model
        self.name = result.nickname
        self.on = False
        self.children = []
        self._apply_info(result.device_info)

    def _apply_info(self, info) -> None:
        if not self._strip:
            self.name = info.nickname or self.name
            self.on = bool(info.device_on)

    async def refresh(self) -> None:
        try:
            self._apply_info(await self._handler.get_device_info())
            if self._strip:
                plugs = await self._handler.get_child_device_list()
                self.children = [
                    Socket(i, p.nickname, bool(p.device_on), p.device_id)
                    for i, p in enumerate(sorted(plugs, key=lambda p: p.position), 1)
                ]
                self.on = any(s.on for s in self.children)
        except Exception as exc:
            raise _tapo_error(exc) from exc

    async def set_power(self, on: bool, socket: Socket | None = None) -> None:
        try:
            if self._strip:
                targets = [socket] if socket else self.children
                for s in targets:
                    plug = await self._handler.plug_unchecked(s.device_id)
                    await (plug.on() if on else plug.off())
            else:
                await (self._handler.on() if on else self._handler.off())
        except Exception as exc:
            raise _tapo_error(exc) from exc


@dataclass
class Discovery:
    """Outcome of a discovery sweep."""

    switches: list[Switch] = field(default_factory=list)
    others: list[str] = field(default_factory=list)  # IPs for the kasa fallback
    errors: list[str] = field(default_factory=list)
    auth_errors: list[str] = field(default_factory=list)


async def _tapo_discover(
    target: str, username: str, password: str, timeout: int
) -> Discovery:
    """Log in to every device answering at `target` (one IP or a broadcast).

    A login rejected by one device is recorded, not raised, so a neighbour's
    plug on another Tapo account can't break discovery of your own devices.
    Nothing is ever retried: devices lock after repeated failed logins.
    """
    client = tapo.ApiClient(username, password, timeout_s=timeout)
    found = Discovery()
    try:
        results = await client.discover_devices(target, timeout_s=timeout)
        async for maybe in results:
            try:
                result = maybe.get()
            except Exception as exc:
                err = _tapo_error(exc)
                bucket = found.auth_errors if isinstance(err, AuthError) else found.errors
                bucket.append(str(err))
                continue
            if isinstance(result, _PLUG_TYPES + _STRIP_TYPES):
                found.switches.append(TapoSwitch(result))
            elif isinstance(result, _UNMODELLED_TYPE):
                # A model tapo doesn't know (e.g. S500/S505 wall switches).
                found.others.append(result.ip)
            # Lights, hubs and cameras are modelled by tapo but aren't
            # switches, so they're skipped.
    except Exception as exc:
        raise _tapo_error(exc) from exc
    return found


# --------------------------------------------------------------------------
# python-kasa fallback
# --------------------------------------------------------------------------


class KasaSwitch(Switch):
    def __init__(self, dev: kasa.Device):
        self._dev = dev
        self._load()

    def _load(self) -> None:
        d = self._dev
        self.name, self.ip, self.model, self.on = d.alias, d.host, d.model, d.is_on
        self.children = [
            Socket(i, c.alias, c.is_on, c.device_id)
            for i, c in enumerate(d.children, 1)
        ]

    async def refresh(self) -> None:
        try:
            await self._dev.update()
        except kasa.AuthenticationError as exc:
            raise AuthError(str(exc)) from exc
        except (kasa.KasaException, OSError, TimeoutError) as exc:
            raise BackendError(str(exc)) from exc
        self._load()

    async def set_power(self, on: bool, socket: Socket | None = None) -> None:
        dev = self._dev.children[socket.index - 1] if socket else self._dev
        try:
            await (dev.turn_on() if on else dev.turn_off())
        except kasa.AuthenticationError as exc:
            raise AuthError(str(exc)) from exc
        except (kasa.KasaException, OSError, TimeoutError) as exc:
            raise BackendError(str(exc)) from exc

    async def close(self) -> None:
        await self._dev.disconnect()


async def _kasa_connect(
    host: str, username: str, password: str, timeout: int
) -> Switch | None:
    try:
        dev = await kasa.Discover.discover_single(
            host,
            credentials=kasa.Credentials(username, password),
            timeout=timeout,
            discovery_timeout=timeout,
        )
    except kasa.UnsupportedDeviceError:
        return None
    except kasa.AuthenticationError as exc:
        raise AuthError(str(exc)) from exc
    except (kasa.KasaException, OSError, TimeoutError) as exc:
        raise BackendError(str(exc)) from exc
    if dev is None:
        return None
    switch = KasaSwitch(dev)
    try:
        await switch.refresh()
    except BaseException:
        await switch.close()
        raise
    return switch


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


async def resolve_ip(host: str) -> str:
    """Return `host` as an IPv4 address (tapo only accepts IPs)."""
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, None, family=socket.AF_INET
        )
    except OSError as exc:
        raise BackendError(f"cannot resolve host '{host}': {exc}") from exc
    return infos[0][4][0]


async def connect(host: str, username: str, password: str, timeout: int) -> Switch:
    """Connect to the device at `host` (IP address or hostname)."""
    ip = await resolve_ip(host)
    found = await _tapo_discover(ip, username, password, timeout)
    if found.switches:
        switch = found.switches[0]
        await switch.refresh()
        return switch
    if found.auth_errors:
        raise AuthError(found.auth_errors[0])
    if found.errors:
        raise BackendError(found.errors[0])
    switch = await _kasa_connect(ip, username, password, timeout)
    if switch is not None:
        return switch
    raise BackendError(f"no supported Tapo plug or switch responded at {host}")


async def discover(
    username: str, password: str, timeout: int, target: str = "255.255.255.255"
) -> Discovery:
    """Find every controllable device on the LAN.

    Per-device failures (including rejected logins) are collected in the
    result instead of aborting the sweep.
    """
    found = await _tapo_discover(target, username, password, timeout)

    async def refresh(s: Switch) -> Switch:
        await s.refresh()
        return s

    async def fallback(ip: str) -> Switch | None:
        return await _kasa_connect(ip, username, password, timeout)

    labels = [s.ip for s in found.switches] + found.others
    outcomes = await asyncio.gather(
        *(refresh(s) for s in found.switches),
        *(fallback(ip) for ip in found.others),
        return_exceptions=True,
    )
    switches: list[Switch] = []
    try:
        for label, outcome in zip(labels, outcomes):
            if isinstance(outcome, AuthError):
                found.auth_errors.append(f"{label}: {outcome}")
            elif isinstance(outcome, BackendError):
                found.errors.append(f"{label}: {outcome}")
            elif isinstance(outcome, BaseException):
                raise outcome
            elif outcome is not None:
                switches.append(outcome)
    except BaseException:
        await asyncio.gather(*(s.close() for s in switches), return_exceptions=True)
        raise
    found.switches = switches
    found.others = []
    return found
