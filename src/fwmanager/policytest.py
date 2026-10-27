"""Live policy test: measure connectivity, apply the profile, measure again, always roll back.

Runs on the client side (the daemon has no network sockets by design). The probes only
talk to your own gateway, your configured DNS resolver and loopback. If this process dies
between apply and rollback, the daemon reverts by itself after the confirmation timeout.
"""

from __future__ import annotations

import ipaddress
import os
import random
import socket
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from fwmanager.cli.dispatch import icmp_echo
from fwmanager.errors import FwError

PROC_ROUTE = Path("/proc/net/route")
PROC_ROUTE6 = Path("/proc/net/ipv6_route")
RESOLV_CONF = Path("/etc/resolv.conf")
DNS_TIMEOUT = 2.0
PING_TIMEOUT = 1.5
TRIES = 2


@dataclass(frozen=True)
class Probe:
    name: str
    ok: bool | None  # None: not applicable on this machine
    detail: str = ""


def default_gateways_v4(route: Path = PROC_ROUTE) -> list[tuple[str, str]]:
    out = []
    try:
        lines = route.read_text().splitlines()[1:]
    except OSError:
        return out
    for line in lines:
        f = line.split()
        if len(f) >= 4 and f[1] == "00000000" and int(f[3], 16) & 2:  # default + RTF_GATEWAY
            out.append((f[0], socket.inet_ntoa(struct.pack("<L", int(f[2], 16)))))
    return out


def default_gateways_v6(route: Path = PROC_ROUTE6) -> list[tuple[str, str]]:
    out = []
    try:
        lines = route.read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        f = line.split()
        if len(f) >= 10 and f[0] == "0" * 32 and f[1] == "00" and f[4] != "0" * 32:
            out.append((f[9], str(ipaddress.IPv6Address(bytes.fromhex(f[4])))))
    return out


def resolvers(conf: Path = RESOLV_CONF) -> list[str]:
    found = []
    try:
        text = conf.read_text()
    except OSError:
        return found
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "nameserver" and "%" not in parts[1]:
            try:
                found.append(str(ipaddress.ip_address(parts[1])))
            except ValueError:
                pass
    # Prefer a real upstream over a loopback stub: the stub never leaves the machine.
    return sorted(found, key=lambda a: ipaddress.ip_address(a).is_loopback)


def dns_query_packet() -> tuple[int, bytes]:
    ident = random.randrange(1, 0xFFFF)
    header = struct.pack("!HHHHHH", ident, 0x0100, 1, 0, 0, 0)
    question = b"\x00" + struct.pack("!HH", 2, 1)  # root zone, type NS: leaks no user data
    return ident, header + question


def probe_gateways(
    version: int, gateways: list[tuple[str, str]], ping: Callable = icmp_echo
) -> Probe:
    name = f"gateway_v{version}"
    if not gateways:
        return Probe(name, None, "no default route")
    iface, addr = gateways[0]
    ok = any(ping(addr, iface, PING_TIMEOUT) for _ in range(TRIES))
    return Probe(name, ok, f"{addr} via {iface}")


def probe_dns(servers: list[str], port: int = 53) -> Probe:
    if not servers:
        return Probe("dns", None, "no resolver configured")
    addr = servers[0]
    fam = socket.AF_INET6 if ":" in addr else socket.AF_INET
    for _ in range(TRIES):
        ident, packet = dns_query_packet()
        try:
            with socket.socket(fam, socket.SOCK_DGRAM) as s:
                s.settimeout(DNS_TIMEOUT)
                s.sendto(packet, (addr, port))
                data, _ = s.recvfrom(1024)
            if len(data) >= 2 and struct.unpack("!H", data[:2])[0] == ident:
                return Probe("dns", True, addr)
        except OSError:
            continue
    return Probe("dns", False, addr)


def probe_loopback(family: int, addr: str, name: str) -> Probe:
    try:
        with socket.socket(family) as srv:
            srv.bind((addr, 0))
            srv.listen(1)
            with socket.socket(family) as cli:
                cli.settimeout(2)
                cli.connect((addr, srv.getsockname()[1]))
    except OSError as exc:
        if exc.errno in (97, 99):  # address family / address not available: not applicable
            return Probe(name, None, "not available")
        return Probe(name, False, addr)
    return Probe(name, True, addr)


def run_probes() -> list[Probe]:
    return [
        probe_gateways(4, default_gateways_v4()),
        probe_gateways(6, default_gateways_v6()),
        probe_dns(resolvers()),
        probe_loopback(socket.AF_INET, "127.0.0.1", "loopback_v4"),
        probe_loopback(socket.AF_INET6, "::1", "loopback_v6"),
    ]


@dataclass
class LiveResult:
    before: list[Probe] = field(default_factory=list)
    after: list[Probe] = field(default_factory=list)
    restored: bool = False

    @property
    def regressions(self) -> list[str]:
        was = {p.name: p.ok for p in self.before}
        return [p.name for p in self.after if was.get(p.name) is True and p.ok is False]

    @property
    def verdict(self) -> str:
        return "unsafe" if self.regressions or not self.restored else "safe"


def run_live_test(client, name: str, probes: Callable[[], list[Probe]] = run_probes) -> LiveResult:
    """Apply ``name`` temporarily. Whatever happens, the previous rules are restored."""
    status = client.status()
    if status["blocking_conflicts"]:
        raise FwError("another firewall is active", code="firewall_conflict")
    if status["pending_profile"]:
        raise FwError("another change is waiting for confirmation", code="apply_pending")

    result = LiveResult(before=probes())
    original = (status["active_profile"], status["firewall_active"])
    client.apply_profile(name)  # starts the daemon's own safety timer
    try:
        result.after = probes()
    finally:
        try:
            client.rollback()
        finally:
            now = client.status()
            result.restored = (now["active_profile"], now["firewall_active"]) == original and not (
                now["pending_profile"]
            )
    return result


def seconds_since(start: float) -> float:
    return time.monotonic() - start


def is_remote_session(env=os.environ) -> bool:
    return bool(env.get("SSH_CONNECTION"))
