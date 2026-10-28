"""Export and import the user's settings as one JSON document.

What travels: the edits to the built-in profiles, the plain-language rules, the remembered
networks (with their routers) and the options (router mode, recording level). What does not:
which profile is active right now, and anything about ufw itself.

An imported document is checked as strictly as the service checks each setting on its own:
nothing in it is trusted, and a single bad entry refuses the whole file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from fwmanager.core import rulebook
from fwmanager.core.backend import Options
from fwmanager.core.profiles import Profile, Source
from fwmanager.core.store import MAX_NETWORKS, Network
from fwmanager.errors import ValidationError

FORMAT = "fwmanager-settings"
FORMAT_VERSION = 1
MAX_BYTES = 512 * 1024


@dataclass(frozen=True)
class Bundle:
    profiles: dict[str, Profile]  # only the built-in profiles that differ from their defaults
    rules: list[rulebook.Rule]
    networks: list[Network]
    options: Options


def _invalid(why: str) -> ValidationError:
    return ValidationError(f"not a valid settings file: {why}", code="invalid_settings")


def export_settings(bundle: Bundle) -> str:
    data = {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "profiles": {
            name: {
                "services": list(p.services),
                "ping": p.ping,
                "mdns": p.mdns,
                "source": {"kind": p.source.kind, "cidrs": list(p.source.cidrs)},
            }
            for name, p in sorted(bundle.profiles.items())
        },
        "rules": [
            {
                "id": r.id,
                "template": r.template,
                "params": {k: (v if isinstance(v, str) else list(v)) for k, v in r.params.items()},
            }
            for r in bundle.rules
        ],
        "networks": [
            {"ssid": n.ssid, "uuid": n.uuid, "profile": n.profile, "routers": list(n.routers)}
            for n in bundle.networks
        ],
        "options": {
            "router_mode": bundle.options.router_mode,
            "log_level": bundle.options.log_level or "",
        },
    }
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _list(value, what: str) -> list:
    if not isinstance(value, list):
        raise _invalid(f"{what} must be a list")
    return value


def _dict(value, what: str) -> dict:
    if not isinstance(value, dict):
        raise _invalid(f"{what} must be an object")
    return value


def _strings(value, what: str) -> tuple[str, ...]:
    items = _list(value, what)
    if not all(isinstance(i, str) for i in items):
        raise _invalid(f"{what} must hold text")
    return tuple(items)


def import_settings(text: str, catalog: dict, defaults: dict[str, Profile]) -> Bundle:
    """Parse and check a document; raises ValidationError (code ``invalid_settings``)."""
    if not isinstance(text, str) or len(text.encode("utf-8", "replace")) > MAX_BYTES:
        raise _invalid("too large")
    try:
        data = json.loads(text)
    except ValueError:
        raise _invalid("not JSON") from None
    data = _dict(data, "the file")
    if data.get("format") != FORMAT or data.get("version") != FORMAT_VERSION:
        raise _invalid("not an fwmanager settings file, or from a newer version")
    try:
        profiles = {}
        for name, raw in _dict(data.get("profiles", {}), "profiles").items():
            if name not in defaults:
                raise _invalid(f"unknown profile {name!r}")
            raw = _dict(raw, "a profile")
            source = _dict(raw.get("source", {}), "a profile source")
            services = _strings(raw.get("services", []), "services")
            if len(services) > len(catalog) or any(s not in catalog for s in services):
                raise _invalid("unknown service")
            ping, mdns = raw.get("ping", False), raw.get("mdns", False)
            if not isinstance(ping, bool) or not isinstance(mdns, bool):
                raise _invalid("ping and mdns must be true or false")
            profiles[name] = Profile(
                name,
                services,
                ping,
                mdns,
                Source(str(source.get("kind", "any")), _strings(source.get("cidrs", []), "cidrs")),
            )

        rules, seen = [], set()
        raw_rules = _list(data.get("rules", []), "rules")
        if len(raw_rules) > rulebook.MAX_RULES:
            raise _invalid("too many rules")
        for raw in raw_rules:
            raw = _dict(raw, "a rule")
            rid, template = raw.get("id"), raw.get("template")
            if not isinstance(rid, str) or not rulebook.RULE_ID_RE.fullmatch(rid) or rid in seen:
                raise _invalid("bad or repeated rule id")
            seen.add(rid)
            if not isinstance(template, str):
                raise _invalid("bad rule template")
            params = rulebook.validate_params(
                template, _dict(raw.get("params", {}), "rule parameters"), catalog,
                frozenset(defaults),
            )  # fmt: skip
            rules.append(rulebook.Rule(rid, template, params))

        networks = []
        raw_networks = _list(data.get("networks", []), "networks")
        if len(raw_networks) > MAX_NETWORKS:
            raise _invalid("too many networks")
        for raw in raw_networks:
            raw = _dict(raw, "a network")
            ssid, uuid, profile = raw.get("ssid", ""), raw.get("uuid", ""), raw.get("profile")
            if not all(isinstance(v, str) for v in (ssid, uuid, profile)):
                raise _invalid("bad network")
            if profile not in defaults:
                raise _invalid(f"unknown profile {profile!r}")
            net = Network(ssid, uuid, profile, _strings(raw.get("routers", []), "routers"))
            if any(n.same_network(net.ssid, net.uuid) for n in networks):
                raise _invalid("a network appears twice")
            networks.append(net)

        raw_options = _dict(data.get("options", {}), "options")
        router_mode = raw_options.get("router_mode", False)
        level = raw_options.get("log_level", "")
        if not isinstance(router_mode, bool) or not isinstance(level, str):
            raise _invalid("bad options")
        options = Options(router_mode=router_mode, log_level=level or None)
    except ValidationError as exc:
        if exc.code == "invalid_settings":
            raise
        raise _invalid(exc.message) from None
    return Bundle(profiles, rules, networks, options)
