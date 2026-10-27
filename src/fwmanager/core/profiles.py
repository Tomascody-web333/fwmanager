"""Profile model and the built-in default profiles."""

from __future__ import annotations

from dataclasses import dataclass, field

from fwmanager.core.validate import validate_cidrs, validate_name
from fwmanager.errors import ValidationError

SOURCE_KINDS = ("any", "lan", "cidrs")


@dataclass(frozen=True)
class Source:
    """Who may reach the allowed services: anyone, the local network, or listed CIDRs."""

    kind: str = "any"
    cidrs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in SOURCE_KINDS:
            raise ValidationError("invalid source kind", code="invalid_source")
        if self.kind == "cidrs":
            object.__setattr__(self, "cidrs", validate_cidrs(list(self.cidrs)))
        elif self.cidrs:
            raise ValidationError("CIDRs only allowed with kind=cidrs", code="invalid_source")


@dataclass(frozen=True)
class Profile:
    name: str
    services: tuple[str, ...] = ()
    ping: bool = False
    mdns: bool = False
    source: Source = field(default_factory=Source)

    def __post_init__(self) -> None:
        validate_name(self.name, "profile")
        for sid in self.services:
            validate_name(sid, "service")
        object.__setattr__(self, "services", tuple(dict.fromkeys(self.services)))
        if not isinstance(self.ping, bool) or not isinstance(self.mdns, bool):
            raise ValidationError("ping/mdns must be booleans", code="invalid_profile")


DEFAULT_PROFILES: dict[str, Profile] = {
    p.name: p
    for p in (
        Profile(
            "casa",
            services=("ssh", "samba", "kdeconnect", "syncthing", "ipp"),
            ping=True,
            mdns=True,
            source=Source("lan"),
        ),
        Profile(
            "trabalho",
            services=("ssh",),
            ping=True,
            mdns=False,
            source=Source("lan"),
        ),
        # Most restrictive: nothing incoming beyond the mandatory exceptions.
        Profile("publico"),
        Profile("personalizado", services=("ssh",), source=Source("lan")),
    )
}
