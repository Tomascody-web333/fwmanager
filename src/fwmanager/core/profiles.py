"""Profile model and the built-in default profiles."""

from __future__ import annotations

from dataclasses import dataclass, field

from fwmanager.core.validate import validate_cidrs, validate_name
from fwmanager.errors import ValidationError

SOURCE_KINDS = ("any", "lan", "cidrs")

# Earlier versions gave the built-in profiles Portuguese ids. Saved state, network choices,
# rules and ufw comments that still use them are read as the English ids they became.
LEGACY_NAMES = {"casa": "home", "trabalho": "work", "publico": "public", "personalizado": "custom"}


def current_name(name):
    """The id a profile has now: legacy Portuguese ids map to English, anything else is kept."""
    return LEGACY_NAMES.get(name, name) if isinstance(name, str) else name


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
            "home",
            services=("ssh", "samba", "kdeconnect", "syncthing", "ipp"),
            ping=True,
            mdns=True,
            source=Source("lan"),
        ),
        Profile(
            "work",
            services=("ssh",),
            ping=True,
            mdns=False,
            source=Source("lan"),
        ),
        # Most restrictive: nothing incoming beyond the mandatory exceptions.
        Profile("public"),
        Profile("custom", services=("ssh",), source=Source("lan")),
    )
}
