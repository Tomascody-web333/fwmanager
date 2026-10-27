"""Translate a profile into ufw rules, and read ufw's own rule listing back.

Every rule fwmanager owns carries the comment ``fwmanager:<profile>``. Anything without that
comment belongs to the user and is never modified or deleted.
"""

from __future__ import annotations

import ipaddress
import re
import shlex
from dataclasses import dataclass

from fwmanager.core.profiles import Profile
from fwmanager.core.services import Service
from fwmanager.core.validate import validate_cidr, validate_name
from fwmanager.errors import ValidationError

COMMENT_PREFIX = "fwmanager:"
LAN_V4 = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16")
LAN_V6 = ("fc00::/7", "fe80::/10")
MAX_MULTIPORT = 15  # iptables multiport limit; a range counts as two


def _port_key(spec: str) -> tuple[int, int]:
    first, _, last = spec.partition(":")
    return int(first), int(last or first)


@dataclass(frozen=True)
class Rule:
    proto: str  # tcp | udp
    source: str  # "any" or a canonical CIDR
    ports: tuple[str, ...]  # "22" or "1714:1764"

    def __post_init__(self) -> None:
        if self.proto not in ("tcp", "udp"):
            raise ValidationError("invalid protocol", code="invalid_protocol")
        if self.source != "any":
            validate_cidr(self.source)
        if not self.ports or len(self.ports) > MAX_MULTIPORT:
            raise ValidationError("invalid port list", code="invalid_port")
        for p in self.ports:
            if not re.fullmatch(r"\d{1,5}(:\d{1,5})?", p):
                raise ValidationError("invalid port", code="invalid_port")
        # ufw stores ports sorted; keep the same canonical order so rules compare equal.
        object.__setattr__(self, "ports", tuple(sorted(self.ports, key=_port_key)))

    @property
    def is_v6(self) -> bool:
        return self.source != "any" and ":" in self.source

    def spec(self) -> list[str]:
        """The rule body shared by add and delete."""
        return [
            "from", self.source, "to", "any", "port", ",".join(self.ports), "proto", self.proto,
        ]  # fmt: skip

    def add_argv(self, profile: str) -> list[str]:
        return ["allow", *self.spec(), "comment", COMMENT_PREFIX + validate_name(profile)]

    def delete_argv(self) -> list[str]:
        return ["delete", "allow", *self.spec()]


def _sources(profile: Profile, ipv6: bool) -> list[str]:
    src = profile.source
    if src.kind == "any":
        return ["any"]
    if src.kind == "lan":
        return [*LAN_V4, *(LAN_V6 if ipv6 else ())]
    return [c for c in src.cidrs if ipv6 or ":" not in c]


def _chunks(ports: list[str]) -> list[tuple[str, ...]]:
    out: list[list[str]] = [[]]
    weight = 0
    for p in ports:
        w = 2 if ":" in p else 1
        if weight + w > MAX_MULTIPORT:
            out.append([])
            weight = 0
        out[-1].append(p)
        weight += w
    return [tuple(c) for c in out if c]


def translate(profile: Profile, catalog: dict[str, Service], ipv6: bool = True) -> list[Rule]:
    """Desired ufw rules for a profile, in a stable order."""
    ports: dict[str, list[str]] = {"tcp": [], "udp": []}
    for sid in profile.services:
        service = catalog.get(sid)
        if service is None:
            raise ValidationError("unknown service", code="unknown_service")
        for pr in service.ports:
            spec = str(pr.first) if pr.first == pr.last else f"{pr.first}:{pr.last}"
            if spec not in ports[pr.proto]:
                ports[pr.proto].append(spec)
    rules = []
    for source in _sources(profile, ipv6):
        for proto in ("tcp", "udp"):
            for chunk in _chunks(ports[proto]):
                rules.append(Rule(proto, source, chunk))
    return rules


# -- reading `ufw show added` ---

_PORTS_RE = re.compile(r"^\d{1,5}(:\d{1,5})?(,\d{1,5}(:\d{1,5})?)*$")


def parse_rule_line(line: str) -> tuple[Rule, str] | None:
    """Parse one ``ufw allow ... comment 'x'`` line of the shape we generate.

    Returns ``(rule, comment)``, or ``None`` for anything else (user rules of other shapes).
    """
    try:
        tok = shlex.split(line)
    except ValueError:
        return None
    if tok[:2] != ["ufw", "allow"]:
        return None
    tok = tok[2:]
    comment = ""
    if len(tok) >= 2 and tok[-2] == "comment":
        comment, tok = tok[-1], tok[:-2]
    # ufw prints "from any to any port P proto T" in its short form "P/T".
    if len(tok) == 1 and (m := re.fullmatch(r"([\d:,]+)/(tcp|udp)", tok[0])):
        if not _PORTS_RE.fullmatch(m.group(1)):
            return None
        try:
            return Rule(m.group(2), "any", tuple(m.group(1).split(","))), comment
        except ValidationError:
            return None
    # from SRC to any port PORTS proto PROTO
    if len(tok) != 8 or tok[0::2][:4] != ["from", "to", "port", "proto"]:
        return None
    _, source, _, dest, _, ports, _, proto = tok
    if dest != "any" or not _PORTS_RE.fullmatch(ports):
        return None
    try:
        if source != "any":
            source = str(ipaddress.ip_network(source, strict=True))
        return Rule(proto, source, tuple(ports.split(","))), comment
    except (ValueError, ValidationError):
        return None


@dataclass
class Listing:
    managed: dict[Rule, str]  # rule -> profile that owns it
    foreign: set[Rule]  # identical-shape rules the user added
    unparsed_managed: int = 0  # ours by comment, but of a shape we cannot reproduce


def parse_added(text: str) -> Listing:
    managed: dict[Rule, str] = {}
    foreign: set[Rule] = set()
    unparsed = 0
    for line in text.splitlines():
        if not line.startswith("ufw "):
            continue
        parsed = parse_rule_line(line)
        ours = f"comment '{COMMENT_PREFIX}" in line or f'comment "{COMMENT_PREFIX}' in line
        if parsed is None:
            unparsed += ours
            continue
        rule, comment = parsed
        if comment.startswith(COMMENT_PREFIX):
            managed[rule] = comment[len(COMMENT_PREFIX) :]
        else:
            foreign.add(rule)
    return Listing(managed, foreign, unparsed)
