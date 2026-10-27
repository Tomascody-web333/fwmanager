"""Generate the nftables script for the ``inet fwmanager`` table.

The output is built only from validated structures: the catalog (ports),
the profile (service ids, flags, CIDRs) and fixed literals. No free-form
text from a client is ever interpolated.
"""

from __future__ import annotations

from dataclasses import dataclass

from fwmanager.core.profiles import Profile
from fwmanager.core.services import Service
from fwmanager.core.validate import validate_name
from fwmanager.errors import ValidationError

FAMILY = "inet"
TABLE = "fwmanager"

LAN_V4 = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16")
LAN_V6 = ("fc00::/7", "fe80::/10")

# Neighbor discovery needs hop limit 255 (proves the packet is on-link).
ICMPV6_ND = ("nd-router-advert", "nd-neighbor-solicit", "nd-neighbor-advert")
ICMPV6_ERRORS = (
    "destination-unreachable",
    "packet-too-big",
    "time-exceeded",
    "parameter-problem",
)
ICMPV6_MLD = ("mld-listener-query",)

PING_RATE = "5/second"
LOG_PREFIX = "fwmanager-blocked: "


@dataclass(frozen=True)
class RenderOptions:
    router_mode: bool = False
    log_blocked: bool = False
    log_rate: str = "10/minute"

    def __post_init__(self) -> None:
        # Fixed shape only: "<n>/second|minute|hour".
        n, _, unit = self.log_rate.partition("/")
        if not (n.isdigit() and 0 < int(n) <= 1000 and unit in ("second", "minute", "hour")):
            raise ValidationError("invalid log rate", code="invalid_option")


def _set(items: tuple[str, ...] | list[str]) -> str:
    return "{ " + ", ".join(items) + " }"


def _source_matches(profile: Profile) -> list[str]:
    """Match prefixes for the profile source; ``[""]`` means no restriction."""
    src = profile.source
    if src.kind == "any":
        return [""]
    if src.kind == "lan":
        return [f"ip saddr {_set(LAN_V4)} ", f"ip6 saddr {_set(LAN_V6)} "]
    v4 = [c for c in src.cidrs if ":" not in c]
    v6 = [c for c in src.cidrs if ":" in c]
    out = []
    if v4:
        out.append(f"ip saddr {_set(v4)} ")
    if v6:
        out.append(f"ip6 saddr {_set(v6)} ")
    return out


def _service_rules(profile: Profile, catalog: dict[str, Service]) -> list[str]:
    ports: dict[str, list[str]] = {"tcp": [], "udp": []}
    for sid in profile.services:
        service = catalog.get(sid)
        if service is None:
            raise ValidationError("unknown service", code="unknown_service")
        for pr in service.ports:
            spec = pr.nft()
            if spec not in ports[pr.proto]:
                ports[pr.proto].append(spec)
    rules = []
    for match in _source_matches(profile):
        for proto in ("tcp", "udp"):
            if ports[proto]:
                rules.append(f"{match}{proto} dport {_set(ports[proto])} counter accept")
    return rules


def render(
    profile: Profile,
    catalog: dict[str, Service],
    options: RenderOptions | None = None,
) -> str:
    """Return the full atomic script: ``add`` + ``delete`` + recreate the table.

    ``add table`` first makes the following ``delete table`` succeed even when
    the table does not exist yet; all three statements run in one transaction.
    """
    options = options or RenderOptions()
    validate_name(profile.name, "profile")

    inp = [
        "ct state established,related counter accept",
        "ct state invalid counter drop",
        'iif "lo" accept',
        f"ip6 hoplimit 255 icmpv6 type {_set(ICMPV6_ND)} accept",
        f"icmpv6 type {_set(ICMPV6_ERRORS + ICMPV6_MLD)} accept",
        # DHCPv4 / DHCPv6 client replies.
        "udp sport 67 udp dport 68 accept",
        "ip6 saddr fe80::/10 udp sport 547 udp dport 546 accept",
    ]
    if profile.ping:
        for match in _source_matches(profile):
            if not match.startswith("ip6"):
                inp.append(f"{match}icmp type echo-request limit rate {PING_RATE} counter accept")
            if not match.startswith("ip "):
                inp.append(f"{match}icmpv6 type echo-request limit rate {PING_RATE} counter accept")
    if profile.mdns:
        inp.append(f"ip saddr {_set(LAN_V4)} udp dport 5353 accept")
        inp.append(f"ip6 saddr {_set(LAN_V6)} udp dport 5353 accept")
    inp += _service_rules(profile, catalog)
    if options.log_blocked:
        inp.append(f'limit rate {options.log_rate} log prefix "{LOG_PREFIX}"')
    inp.append("counter")  # counts what the chain policy drops

    if options.router_mode:
        fwd_policy = "drop"
        fwd = ["ct state established,related accept", "ct state invalid drop"]
    else:
        fwd_policy = "accept"
        fwd = []

    def chain(name: str, hook: str, policy: str, rules: list[str], comment: str = "") -> list[str]:
        head = f"\tchain {name} {{"
        if comment:
            head += f' comment "{comment}";'
        lines = [
            head,
            f"\t\ttype filter hook {hook} priority filter; policy {policy};",
        ]
        lines += [f"\t\t{r}" for r in rules]
        lines.append("\t}")
        return lines

    body = [f'\tcomment "profile:{profile.name}";']
    body += chain("input", "input", "drop", inp)
    body += chain("forward", "forward", fwd_policy, fwd)
    body += chain("output", "output", "accept", [])
    return "\n".join(
        [
            f"add table {FAMILY} {TABLE}",
            f"delete table {FAMILY} {TABLE}",
            f"table {FAMILY} {TABLE} {{",
            *body,
            "}",
            "",
        ]
    )
