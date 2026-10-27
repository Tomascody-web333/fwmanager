"""Static policy check: will this profile keep a normal machine working?

Nothing here changes the system. The generated script is parsed back and verified against
the invariants a client machine needs (replies, loopback, IPv6 neighbour discovery, DHCP,
unrestricted output), the nft parser/kernel is asked to dry-run it (``nft -c``), and the
environment is inspected for known problems (other firewalls, SSH reconnect).
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from fwmanager.core.conflicts import Conflicts
from fwmanager.core.nft import Nft
from fwmanager.core.profiles import Profile
from fwmanager.core.rules import FAMILY, LAN_V4, LAN_V6, TABLE, RenderOptions, render
from fwmanager.core.services import Service
from fwmanager.errors import FwError

ERROR, WARNING, INFO = "error", "warning", "info"

# Verbs that must never appear in a generated policy: it is a pure filter.
FORBIDDEN_VERBS = ("flush", "reject", "masquerade", "dnat", "snat", "redirect", "tproxy", "queue",
                   "jump", "goto")  # fmt: skip
ALLOWED_TOP_LEVEL = (
    f"add table {FAMILY} {TABLE}",
    f"delete table {FAMILY} {TABLE}",
    f"table {FAMILY} {TABLE} {{",
)
CHAIN_RE = re.compile(r"^chain (\w+) \{(?: comment \"[^\"]*\";)?$")
HOOK_RE = re.compile(r"^type filter hook (\w+) priority filter; policy (\w+);$")


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    detail: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def add(self, severity: str, code: str, detail: str) -> None:
        self.findings.append(Finding(severity, code, detail))

    @property
    def verdict(self) -> str:
        severities = {f.severity for f in self.findings}
        if ERROR in severities:
            return "unsafe"
        return "warnings" if WARNING in severities else "safe"


def _parse_chains(script: str) -> dict[str, dict]:
    chains: dict[str, dict] = {}
    current = None
    for raw in script.splitlines():
        line = raw.strip()
        if m := CHAIN_RE.match(line):
            current = chains.setdefault(m.group(1), {"hook": None, "policy": None, "rules": []})
        elif current is not None and (h := HOOK_RE.match(line)):
            current["hook"], current["policy"] = h.group(1), h.group(2)
        elif line == "}":
            current = None
        elif current is not None and line:
            current["rules"].append(line)
    return chains


def _has(rules: list[str], *needles: str, verdict: str = "accept") -> bool:
    return any(all(n in r for n in needles) and r.endswith(verdict) for r in rules)


def analyze_script(script: str, expect_router: bool = False) -> Report:
    """Check a generated script against the must-have invariants."""
    report = Report()
    lines = [ln.strip() for ln in script.splitlines() if ln.strip()]

    # 1. Only our own table may be touched, and only with filter statements.
    for ln in lines:
        top_level = not ln.startswith(("chain", "type", "comment", "}", "ct ", "iif", "ip", "icmp",
                                       "udp", "tcp", "limit", "counter"))  # fmt: skip
        if top_level and ln not in ALLOWED_TOP_LEVEL:
            report.add(ERROR, "foreign_statement", f"unexpected top-level statement: {ln[:60]}")
        words = re.findall(r"[a-z0-9]+", ln.lower())
        for verb in FORBIDDEN_VERBS:
            if verb in words:
                report.add(ERROR, "forbidden_verb", f"statement uses '{verb}'")
    if not all(s in lines for s in ALLOWED_TOP_LEVEL):
        report.add(ERROR, "not_atomic", "the add/delete/recreate sequence is incomplete")

    chains = _parse_chains(script)
    inp, fwd, out = chains.get("input"), chains.get("forward"), chains.get("output")

    # 2. Output must stay open; forward must not break Docker/VMs unless router mode is on.
    if out is None or out["hook"] != "output":
        report.add(ERROR, "no_output_chain", "output chain missing")
    elif out["policy"] != "accept" or any(r.endswith(("drop", "reject")) for r in out["rules"]):
        report.add(ERROR, "output_blocked", "outgoing traffic would be blocked")
    if fwd is not None:
        if fwd["policy"] == "drop" and not expect_router:
            report.add(ERROR, "forward_blocked", "forwarding is dropped outside router mode")
        elif fwd["policy"] == "drop":
            report.add(INFO, "router_mode", "router mode: Docker/VM/containers may be affected")

    # 3. The input chain must carry every mandatory exception.
    if inp is None or inp["hook"] != "input":
        report.add(ERROR, "no_input_chain", "input chain missing")
        return report
    if inp["policy"] != "drop":
        report.add(WARNING, "input_not_dropping", "input policy is not drop: no protection")
    rules = inp["rules"]
    required = [
        ("missing_established", "replies to your own traffic (established/related) not allowed",
         _has(rules, "ct state established,related")),
        ("missing_invalid_drop", "invalid packets are not dropped",
         _has(rules, "ct state invalid", verdict="drop")),
        ("missing_loopback", "loopback traffic not allowed", _has(rules, 'iif "lo"')),
        ("missing_dhcp4", "DHCPv4 replies not allowed (renewing the lease would fail)",
         _has(rules, "udp sport 67", "udp dport 68")),
        ("missing_dhcp6", "DHCPv6 replies not allowed",
         _has(rules, "udp sport 547", "udp dport 546")),
    ]  # fmt: skip
    for name in ("nd-neighbor-solicit", "nd-neighbor-advert", "nd-router-advert"):
        required.append((
            f"missing_{name.replace('-', '_')}",
            f"IPv6 {name} not allowed (IPv6 would stop working)",
            _has(rules, "ip6 hoplimit 255", "icmpv6 type", name),
        ))  # fmt: skip
    for name in ("packet-too-big", "destination-unreachable", "time-exceeded", "parameter-problem"):
        required.append((
            f"missing_{name.replace('-', '_')}",
            f"ICMPv6 {name} not allowed (IPv6 connections would hang)",
            _has(rules, "icmpv6 type", name),
        ))  # fmt: skip
    for code, detail, ok in required:
        if not ok:
            report.add(ERROR, code, detail)
    return report


def _nft_dry_run(script: str, nft: Nft, report: Report) -> None:
    try:
        nft.run(["-c", "-f", "-"], stdin=script)
    except FwError as exc:
        report.add(ERROR, "nft_rejects_rules", f"nft refused the rules: {exc.message[:120]}")


def exposure_findings(profile: Profile, catalog: dict[str, Service]) -> list[Finding]:
    out = []
    if profile.services and profile.source.kind == "any":
        detail = "services are reachable from any network, not only the local one"
        out.append(Finding(WARNING, "exposed_to_any", detail))
    if profile.source.kind == "cidrs" and any(
        ipaddress.ip_network(c).prefixlen == 0 for c in profile.source.cidrs
    ):
        out.append(Finding(WARNING, "exposed_to_any", "a CIDR matches the whole internet"))
    return out


def ssh_findings(
    profile: Profile, catalog: dict[str, Service], env: Mapping[str, str]
) -> list[Finding]:
    """Established sessions survive a change; a *new* SSH connection might not."""
    parts = env.get("SSH_CONNECTION", "").split()
    if len(parts) != 4:
        return []
    try:
        client = ipaddress.ip_address(parts[0])
        port = int(parts[3])
    except ValueError:
        return []
    service_open = any(
        pr.proto == "tcp" and pr.first <= port <= pr.last
        for sid in profile.services
        if (svc := catalog.get(sid))
        for pr in svc.ports
    )
    src = profile.source
    if src.kind == "any":
        source_ok = True
    elif src.kind == "lan":
        nets = LAN_V4 if client.version == 4 else LAN_V6
        source_ok = any(client in ipaddress.ip_network(n) for n in nets)
    else:
        source_ok = any(client in ipaddress.ip_network(c, strict=False) for c in src.cidrs)
    if service_open and source_ok:
        return [Finding(INFO, "ssh_ok", "your SSH session and future reconnects stay allowed")]
    detail = "your current SSH session survives, but you could not open a new one"
    return [Finding(WARNING, "ssh_reconnect_blocked", detail)]


def check_profile(
    profile: Profile,
    catalog: dict[str, Service],
    options: RenderOptions | None = None,
    nft: Nft | None = None,
    conflicts: Conflicts | None = None,
) -> Report:
    options = options or RenderOptions()
    report = Report()
    try:
        script = render(profile, catalog, options)
    except FwError as exc:
        report.add(ERROR, exc.code, exc.message)
        return report
    report.findings += analyze_script(script, expect_router=options.router_mode).findings
    if nft is not None:
        _nft_dry_run(script, nft, report)
    report.findings += exposure_findings(profile, catalog)
    if conflicts is not None:
        if conflicts.blocking:
            names = ", ".join(conflicts.blocking)
            report.add(ERROR, "firewall_conflict", f"another firewall is active: {names}")
        for name in conflicts.warnings:
            severity = WARNING if options.router_mode else INFO
            report.add(severity, f"{name}_present", f"{name} has its own rules (left untouched)")
    return report
