"""Static policy check: will this profile keep a normal machine working?

Nothing here changes the system. It reads ufw's real configuration and the plan the backend
would carry out, and checks that the essentials a client machine needs survive: replies,
loopback, DHCP, IPv6 neighbour discovery, ICMP errors, open output. ``ufw --dry-run`` validates
every rule we would add, and the environment is inspected for known problems.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from fwmanager.core import ufw_files
from fwmanager.core.backend import Options, UfwBackend
from fwmanager.core.conflicts import Conflicts
from fwmanager.core.profiles import Profile
from fwmanager.core.services import Service
from fwmanager.core.ufw_rules import LAN_V4, LAN_V6, Rule
from fwmanager.errors import FwError

ERROR, WARNING, INFO = "error", "warning", "info"
MAX_LISTED = 12  # rules named in a finding before it says "and N more"


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


def _live_lines(text: str) -> list[str]:
    """Rule lines with our own managed block removed (it is judged separately)."""
    return [
        ln.strip()
        for ln in ufw_files.strip_block(text).splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


def _has(lines: list[str], *needles: str, verdict: str = "ACCEPT") -> bool:
    return any(all(n in ln for n in needles) and ln.endswith(f"-j {verdict}") for ln in lines)


V4_ESSENTIALS = [
    ("missing_loopback", ("-i lo",), "ACCEPT"),
    ("missing_established", ("--ctstate RELATED,ESTABLISHED",), "ACCEPT"),
    ("missing_invalid_drop", ("--ctstate INVALID",), "DROP"),
    ("missing_dhcp4", ("--sport 67", "--dport 68"), "ACCEPT"),
    ("missing_destination_unreachable", ("--icmp-type destination-unreachable",), "ACCEPT"),
    ("missing_time_exceeded", ("--icmp-type time-exceeded",), "ACCEPT"),
]
V6_ESSENTIALS = [
    ("missing_loopback", ("-i lo",), "ACCEPT"),
    ("missing_established", ("--ctstate RELATED,ESTABLISHED",), "ACCEPT"),
    ("missing_invalid_drop", ("--ctstate INVALID",), "DROP"),
    ("missing_dhcp6", ("--sport 547", "--dport 546"), "ACCEPT"),
    ("missing_nd_neighbor_solicit", ("neighbor-solicitation", "--hl-eq 255"), "ACCEPT"),
    ("missing_nd_neighbor_advert", ("neighbor-advertisement", "--hl-eq 255"), "ACCEPT"),
    ("missing_nd_router_advert", ("router-advertisement", "--hl-eq 255"), "ACCEPT"),
    ("missing_packet_too_big", ("--icmpv6-type packet-too-big",), "ACCEPT"),
    ("missing_destination_unreachable", ("--icmpv6-type destination-unreachable",), "ACCEPT"),
    ("missing_time_exceeded", ("--icmpv6-type time-exceeded",), "ACCEPT"),
]

BLOCK_LINE_RE = re.compile(
    r"^-A ufw6?-before-input "
    r"(-p icmp --icmp-type echo-request"
    r"|-p icmpv6 --icmpv6-type echo-request"
    r"|-p udp -d (224\.0\.0\.251|ff02::fb) --dport 5353) -j DROP$"
)


def analyze_config(config_dir: Path, ipv6: bool) -> Report:
    """Check ufw's own before-rules (plus our block) against the must-have list."""
    report = Report()
    files = [("before.rules", ufw_files.ANCHOR_V4, V4_ESSENTIALS)]
    if ipv6:
        files.append(("before6.rules", ufw_files.ANCHOR_V6, V6_ESSENTIALS))
    seen: set[str] = set()
    for name, anchor, essentials in files:
        try:
            text = (config_dir / name).read_text()
        except OSError:
            report.add(ERROR, "ufw_unsupported", f"cannot read {name}")
            continue
        lines = _live_lines(text)
        if anchor not in lines:
            report.add(ERROR, "ufw_unsupported", f"{name} was edited: the safe anchor is missing")
        for code, needles, verdict in essentials:
            if not _has(lines, *needles, verdict=verdict) and code not in seen:
                seen.add(code)
                report.add(ERROR, code, f"{name} lacks the rule for {code[8:]}")
        # Our own block may only drop ping and mDNS; anything else is not ours.
        match = ufw_files.BLOCK_RE.search(text)
        if match:
            body = match.group(0).splitlines()[1:-1]
            for ln in body:
                if ln and not ln.startswith("#") and not BLOCK_LINE_RE.match(ln):
                    report.add(
                        ERROR, "foreign_statement", f"unexpected line in managed block: {ln[:60]}"
                    )
    return report


def _describe(rule: Rule) -> str:
    # Language-neutral on purpose (it is shown as-is in every language): "source → port".
    return f"{rule.source} → {rule.proto} {','.join(rule.ports)}"


def _listed(rules: list[Rule]) -> str:
    names = [_describe(r) for r in rules[:MAX_LISTED]]
    more = len(rules) - MAX_LISTED
    return "; ".join(names) + (f" (and {more} more)" if more > 0 else "")


def exposure_findings(profile: Profile) -> list[Finding]:
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
    backend: UfwBackend,
    options: Options | None = None,
    conflicts: Conflicts | None = None,
) -> Report:
    options = options or Options()
    report = Report()
    ufw = backend.ufw
    if not ufw.installed():
        report.add(ERROR, "ufw_missing", "ufw is not installed")
        return report
    if conflicts is not None:
        if conflicts.blocking:
            names = ", ".join(conflicts.blocking)
            report.add(ERROR, "firewall_conflict", f"another firewall manager is active: {names}")
        for name in conflicts.warnings:
            severity = WARNING if options.router_mode else INFO
            report.add(severity, f"{name}_present", f"{name} has its own rules (left untouched)")

    try:
        plan = backend.plan(profile)
    except FwError as exc:
        report.add(ERROR, exc.code, exc.message)
        return report

    ipv6 = ufw.ipv6_enabled()
    report.findings += analyze_config(ufw.config_dir, ipv6).findings
    for rule in plan.add:  # ufw's own parser validates each rule we would create
        try:
            ufw.run(["--dry-run", *rule.add_argv(profile.name)])
        except FwError as exc:
            report.add(ERROR, "ufw_rejects_rules", f"ufw refused a rule: {exc.message[:100]}")
            break
    if plan.add:
        report.add(INFO, "will_add", _listed(plan.add))
    if plan.remove:
        report.add(INFO, "will_remove", _listed(plan.remove))
    if not ufw.is_active():
        report.add(INFO, "ufw_will_be_enabled", "ufw is off and will be turned on")
    if not ipv6:
        report.add(WARNING, "ipv6_not_filtered", "IPv6 is not enabled in ufw: it is not filtered")
    if backend.listing().unparsed_managed:
        report.add(WARNING, "unparsed_rules", "some fwmanager rules have an unexpected shape")
    if options.router_mode:
        report.add(INFO, "router_mode", "router mode: Docker/VM/containers may be affected")
    report.findings += exposure_findings(profile)
    return report
