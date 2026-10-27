"""Plain-language rules: fixed templates with validated parameters, compiled onto profiles.

There is no free-text parsing. A rule is a template id plus parameters chosen from fixed lists
(services from the catalog, known profile names, a scope) or a validated CIDR. Rules are applied
on top of the base profiles (built-in ones plus the user's edits), in order; a later rule wins
over an earlier one for the same setting. The result is an ordinary ``Profile``, so everything
downstream (safety checks, the 30 s confirmation, the policy test) works unchanged.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from fwmanager.core.profiles import Profile, Source
from fwmanager.core.services import Service
from fwmanager.core.validate import validate_cidrs, validate_name
from fwmanager.errors import ValidationError

MAX_RULES = 64
RULE_ID_RE = re.compile(r"^[a-z0-9-]{1,16}$")

ALLOW_ONLY_IN = "allow_only_in"
BLOCK_ALL_EXCEPT = "block_all_except"
RESTRICT_SOURCE = "restrict_source"
HIDE = "hide"
CLOSE_SERVICE = "close_service"

# template -> {parameter: kind}. Kinds: service, services, profiles, scope, cidrs.
SCHEMAS: dict[str, dict[str, str]] = {
    ALLOW_ONLY_IN: {"service": "service", "profiles": "profiles"},
    BLOCK_ALL_EXCEPT: {"services": "services", "profiles": "profiles"},
    RESTRICT_SOURCE: {"scope": "scope", "cidrs": "cidrs", "profiles": "profiles"},
    HIDE: {"profiles": "profiles"},
    CLOSE_SERVICE: {"service": "service"},
}
TEMPLATE_ORDER = (ALLOW_ONLY_IN, BLOCK_ALL_EXCEPT, RESTRICT_SOURCE, HIDE, CLOSE_SERVICE)
SCOPES = ("lan", "cidrs")


def _invalid(code: str = "invalid_rule") -> ValidationError:
    return ValidationError("invalid rule", code=code)


@dataclass(frozen=True)
class Rule:
    id: str
    template: str
    params: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not RULE_ID_RE.fullmatch(self.id or ""):
            raise _invalid()


def validate_params(
    template: str,
    params: Mapping[str, object],
    catalog: Mapping[str, Service],
    profile_names: set[str] | frozenset[str],
) -> dict[str, object]:
    """Return a clean copy of ``params`` or raise ``ValidationError``. Unknown keys are refused."""
    schema = SCHEMAS.get(template)
    if schema is None:
        raise _invalid("unknown_template")
    needed = set(schema)
    if template == RESTRICT_SOURCE and params.get("scope") != "cidrs":
        needed.discard("cidrs")  # "the local network" needs no list of networks
    if set(params) != needed:
        raise _invalid()
    out: dict[str, object] = {}
    for key, value in params.items():
        kind = schema[key]
        if kind == "service":
            sid = validate_name(value, "service")
            if sid not in catalog:
                raise _invalid("unknown_service")
            out[key] = sid
        elif kind == "services":
            if not isinstance(value, (list, tuple)) or len(value) > len(catalog):
                raise _invalid()
            sids = tuple(dict.fromkeys(validate_name(v, "service") for v in value))
            if any(s not in catalog for s in sids):
                raise _invalid("unknown_service")
            out[key] = sids
        elif kind == "profiles":
            if not isinstance(value, (list, tuple)) or not value:
                raise _invalid()
            names = tuple(dict.fromkeys(validate_name(v, "profile") for v in value))
            if any(n not in profile_names for n in names):
                raise _invalid("unknown_profile")
            out[key] = names
        elif kind == "scope":
            if value not in SCOPES:
                raise _invalid("invalid_source")
            out[key] = value
        elif kind == "cidrs":
            if not isinstance(value, (list, tuple)):
                raise _invalid("invalid_cidr")
            out[key] = validate_cidrs(list(value))
    return out


def _targets(rule_params: Mapping[str, object], profile: Profile) -> bool:
    profiles = rule_params.get("profiles")
    return profiles is None or profile.name in profiles  # type: ignore[operator]


def apply_rule(rule: Rule, profile: Profile) -> Profile:
    """The profile as this one rule leaves it."""
    p = rule.params
    if rule.template == ALLOW_ONLY_IN:
        sid = p["service"]
        keep = [s for s in profile.services if s != sid]
        services = (*keep, sid) if profile.name in p["profiles"] else tuple(keep)
        return replace(profile, services=tuple(dict.fromkeys(services)))
    if rule.template == BLOCK_ALL_EXCEPT:
        return replace(profile, services=tuple(p["services"])) if _targets(p, profile) else profile
    if rule.template == RESTRICT_SOURCE:
        if not _targets(p, profile):
            return profile
        if p["scope"] == "lan":
            return replace(profile, source=Source("lan"))
        return replace(profile, source=Source("cidrs", tuple(p["cidrs"])))
    if rule.template == HIDE:
        return replace(profile, ping=False, mdns=False) if _targets(p, profile) else profile
    if rule.template == CLOSE_SERVICE:
        sid = p["service"]
        return replace(profile, services=tuple(s for s in profile.services if s != sid))
    raise _invalid("unknown_template")


def compile_profiles(base: Mapping[str, Profile], rules: list[Rule]) -> dict[str, Profile]:
    """Base profiles with every rule applied, in order."""
    out = dict(base)
    for rule in rules:
        out = {name: apply_rule(rule, prof) for name, prof in out.items()}
    return out


def next_rule_id(rules: list[Rule]) -> str:
    used = {r.id for r in rules}
    n = 1
    while f"r{n}" in used:
        n += 1
    return f"r{n}"


@dataclass(frozen=True)
class ProfileChange:
    name: str
    opened: tuple[str, ...]
    closed: tuple[str, ...]
    ping: bool | None  # new value, or None if unchanged
    mdns: bool | None
    source: Source | None  # new source, or None if unchanged


def diff_profiles(
    before: Mapping[str, Profile], after: Mapping[str, Profile]
) -> list[ProfileChange]:
    """What a rule would change, per profile (only the profiles that really change)."""
    out = []
    for name, new in after.items():
        old = before[name]
        if old == new:
            continue
        out.append(
            ProfileChange(
                name,
                tuple(s for s in new.services if s not in old.services),
                tuple(s for s in old.services if s not in new.services),
                new.ping if new.ping != old.ping else None,
                new.mdns if new.mdns != old.mdns else None,
                new.source if new.source != old.source else None,
            )
        )
    return out


def usable_rules(
    rules: list[Rule], catalog: Mapping[str, Service], profile_names: frozenset[str] | set[str]
) -> tuple[list[Rule], int]:
    """Drop rules that no longer validate (a service left the catalog). Returns (kept, dropped)."""
    kept = []
    for r in rules:
        try:
            kept.append(
                Rule(
                    r.id, r.template, validate_params(r.template, r.params, catalog, profile_names)
                )
            )
        except ValidationError:
            continue
    return kept, len(rules) - len(kept)
