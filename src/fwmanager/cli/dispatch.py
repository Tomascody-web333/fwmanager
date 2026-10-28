"""`fwmanager-cli dispatch`: apply the profile of the network that just came up.

Called by the NetworkManager dispatcher as root. Everything NetworkManager passes in
(interface, connection UUID, gateways) is validated before use; the only thing sent to
the daemon is a profile *name* chosen from the saved network mapping.

Flow: read the router's hardware address (one ping first if the kernel has not learned it yet)
-> ask the daemon for the network's profile, giving it that address -> ping gateways
(baseline) -> apply -> ping again -> confirm if still reachable, otherwise roll back at once.
An unknown wired connection keeps the current profile. A remembered Wi-Fi whose router is not
one seen there before is treated as unknown (a fake access point can copy a name and a
password, not the router behind it).
If nobody confirms, the daemon reverts by itself after the timeout, so a killed script cannot
lock the user out.
"""

from __future__ import annotations

import ipaddress
import os
import random
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from fwmanager.client import Client
from fwmanager.core.store import Network, validate_mac
from fwmanager.errors import FwError, ValidationError

EXIT_OK, EXIT_ERROR, EXIT_REVERTED = 0, 1, 2

UNKNOWN_NETWORK_PROFILE = "public"  # most restrictive profile for networks we do not know
# A cable you plug in is rarely a stranger's network, and switching a wired machine to the
# strictest profile can shut an SSH session out. Unknown wired connections keep the current
# profile; only Wi-Fi, mobile broadband and anything unidentified fall back to the strict one.
WIRED_KINDS = ("802-3-ethernet", "bond", "team", "vlan", "bridge")
KIND_RE = re.compile(r"^[a-z0-9-]{1,32}$")
IFACE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")
PING_TRIES = 3
PING_TIMEOUT = 1.5
BASELINE_TIMEOUT = 2.0
PENDING_WAIT = 15  # seconds to wait for another pending change to finish
NMCLI_PATHS = ("/usr/bin/nmcli", "/bin/nmcli")
ARP_TABLE = "/proc/net/arp"
ARP_COMPLETE = 0x2


def log(msg: str) -> None:
    print(f"fwmanager-dispatch: {msg}", file=sys.stderr, flush=True)


@dataclass(frozen=True)
class Gateway:
    address: str
    version: int


@dataclass(frozen=True)
class Context:
    iface: str
    uuid: str
    gateways: tuple[Gateway, ...] = field(default_factory=tuple)


def parse_context(iface: str, env: Mapping[str, str]) -> Context:
    """Validate what NetworkManager passed us. Bad optional values are dropped."""
    if not IFACE_RE.fullmatch(iface or ""):
        raise ValidationError("invalid interface name", code="invalid_input")
    uuid = env.get("CONNECTION_UUID", "")
    try:
        Network("x", uuid, "public") if uuid else None  # reuse the canonical-UUID check
    except ValidationError:
        uuid = ""
    gateways = []
    for key, version in (("IP4_GATEWAY", 4), ("IP6_GATEWAY", 6)):
        raw = env.get(key, "").strip()
        if not raw:
            continue
        try:
            addr = ipaddress.ip_address(raw.split("%")[0])
        except ValueError:
            continue
        if addr.version == version and not addr.is_unspecified and not addr.is_multicast:
            gateways.append(Gateway(str(addr), version))
    return Context(iface, uuid, tuple(gateways))


def _nmcli_field(uuid: str, field_name: str, run: Callable) -> str:
    """One field of a connection via nmcli (unescaped); empty string when unknown."""
    nmcli = next((p for p in NMCLI_PATHS if os.access(p, os.X_OK)), None) or shutil.which("nmcli")
    if not uuid or not nmcli:
        return ""
    try:
        out = run(
            [nmcli, "-g", field_name, "connection", "show", "uuid", uuid],
            capture_output=True, text=True, timeout=5, check=False,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if out.returncode != 0:
        return ""
    return out.stdout.rstrip("\n").replace("\\:", ":").replace("\\\\", "\\")


def lookup_ssid(uuid: str, run: Callable = subprocess.run) -> str:
    """Wi-Fi SSID of a connection via nmcli; empty string when unknown or not Wi-Fi."""
    ssid = _nmcli_field(uuid, "802-11-wireless.ssid", run)
    try:
        Network(ssid, "", "public") if ssid else None
    except ValidationError:
        return ""
    return ssid


def lookup_kind(uuid: str, run: Callable = subprocess.run) -> str:
    """NetworkManager's connection type (``802-3-ethernet``, ``802-11-wireless``...)."""
    kind = _nmcli_field(uuid, "connection.type", run)
    return kind if KIND_RE.fullmatch(kind) else ""


def router_mac(gateway: str, iface: str, table: str = ARP_TABLE) -> str:
    """The hardware address the kernel learned for the IPv4 gateway on ``iface``, or ""."""
    try:
        lines = open(table, encoding="ascii", errors="replace").read().splitlines()[1:]
    except OSError:
        return ""
    for line in lines:
        fields = line.split()
        if len(fields) < 6 or fields[0] != gateway or fields[5] != iface:
            continue
        try:
            if not int(fields[2], 16) & ARP_COMPLETE:
                continue
            return validate_mac(fields[3].lower())
        except (ValueError, ValidationError):
            return ""
    return ""


# -- ICMP echo ---


def checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return ~total & 0xFFFF


def build_echo_v4(ident: int, seq: int) -> bytes:
    header = struct.pack("!BBHHH", 8, 0, 0, ident, seq)
    payload = b"fwmanager"
    return struct.pack("!BBHHH", 8, 0, checksum(header + payload), ident, seq) + payload


def icmp_echo(addr: str, iface: str, timeout: float) -> bool:
    """One ICMP/ICMPv6 echo to ``addr``; True if a matching reply arrives in time."""
    ip = ipaddress.ip_address(addr)
    ident, seq = os.getpid() & 0xFFFF, random.randrange(1, 0xFFFF)
    try:
        if ip.version == 4:
            sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
            packet, dest, reply_type = build_echo_v4(ident, seq), (addr, 0), 0
        else:
            sock = socket.socket(socket.AF_INET6, socket.SOCK_RAW, socket.IPPROTO_ICMPV6)
            scope = socket.if_nametoindex(iface) if ip.is_link_local else 0
            # The kernel fills in the ICMPv6 checksum on raw ICMPv6 sockets.
            packet = struct.pack("!BBHHH", 128, 0, 0, ident, seq) + b"fwmanager"
            dest, reply_type = (addr, 0, 0, scope), 129
    except OSError:
        return False
    with sock:
        try:
            sock.sendto(packet, dest)
        except OSError:
            return False
        deadline = time.monotonic() + timeout
        while (left := deadline - time.monotonic()) > 0:
            sock.settimeout(left)
            try:
                data, _src = sock.recvfrom(1024)
            except (TimeoutError, OSError):
                return False
            if ip.version == 4:
                data = data[(data[0] & 0x0F) * 4 :]  # strip the IPv4 header
            if len(data) >= 8:
                typ, _code, _ck, rid, rseq = struct.unpack("!BBHHH", data[:8])
                if typ == reply_type and rid == ident and rseq == seq:
                    return True
    return False


def reachable(gw: Gateway, iface: str, tries: int, timeout: float, ping: Callable) -> bool:
    return any(ping(gw.address, iface, timeout) for _ in range(tries))


# -- main flow ---


def wait_for_idle(client, seconds: int, sleep: Callable[[float], None]) -> bool:
    for _ in range(seconds):
        if not client.status()["pending_profile"]:
            return True
        sleep(1)
    return not client.status()["pending_profile"]


def dispatch(
    client,
    ctx: Context,
    ssid: str = "",
    ping: Callable = icmp_echo,
    sleep: Callable[[float], None] = time.sleep,
    kind: str = "",
    router: Callable[[str, str], str] | None = None,
) -> int:
    router = router or router_mac
    v4 = next((g.address for g in ctx.gateways if g.version == 4), "")
    mac = router(v4, ctx.iface) if v4 else ""
    if v4 and not mac:  # not in the kernel's neighbour table yet: one ping teaches it
        ping(v4, ctx.iface, BASELINE_TIMEOUT)
        mac = router(v4, ctx.iface)

    # A network we cannot identify at all falls back to the restrictive profile.
    mapped, verdict = client.check_network(ssid, ctx.uuid, mac) if (ssid or ctx.uuid) else ("", "")
    if verdict in ("changed", "unverified"):
        log(f"the router on {ctx.iface} is not the one remembered for this network")
    if not mapped and kind in WIRED_KINDS:
        log(f"wired connection without a profile on {ctx.iface}; keeping the current profile")
        return EXIT_OK
    target = mapped or UNKNOWN_NETWORK_PROFILE
    status = client.status()
    if status["blocking_conflicts"]:
        log("another firewall is active; leaving it alone")
        return EXIT_OK
    if status["active_profile"] == target and not status["pending_profile"]:
        log(f"profile {target} already active on {ctx.iface}")
        return EXIT_OK
    if not wait_for_idle(client, PENDING_WAIT, sleep):
        log("another change is waiting for confirmation; giving up")
        return EXIT_ERROR

    # Only gateways that answered before the change can be used to judge the change.
    checkable = [g for g in ctx.gateways if ping(g.address, ctx.iface, BASELINE_TIMEOUT)]
    if ctx.gateways and not checkable:
        log("gateway does not answer ping even before the change; cannot verify connectivity")
    elif not ctx.gateways:
        log("no gateway reported; cannot verify connectivity")

    log(f"applying profile {target} on {ctx.iface}")
    client.apply_profile(target)

    broken = [g for g in checkable if not reachable(g, ctx.iface, PING_TRIES, PING_TIMEOUT, ping)]
    if broken:
        log(f"gateway unreachable after applying {target}; rolling back")
        client.rollback()
        return EXIT_REVERTED
    client.confirm()
    log(f"profile {target} confirmed")
    return EXIT_OK


def run(iface: str, bus_address: str | None = None, env: Mapping[str, str] | None = None) -> int:
    ctx = parse_context(iface, os.environ if env is None else env)
    client = Client(bus_address)
    try:
        return dispatch(client, ctx, lookup_ssid(ctx.uuid), kind=lookup_kind(ctx.uuid))
    except FwError as exc:
        log(f"failed: {exc.code}")
        return EXIT_ERROR
