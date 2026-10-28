"""Persistent state: last confirmed profile and the network -> profile mapping.

Files live in the daemon's state directory (``/var/lib/fwmanager``). Writes are
atomic (temp file + fsync + rename) so a crash never leaves a half-written file.
"""

from __future__ import annotations

import json
import os
import re
import uuid as uuid_mod
from dataclasses import dataclass
from pathlib import Path

from fwmanager.core.backend import Options
from fwmanager.core.profiles import Profile, Source, current_name
from fwmanager.core.rulebook import MAX_RULES, Rule
from fwmanager.core.validate import validate_name
from fwmanager.errors import StoreCorrupt, StoreError, ValidationError

PROFILES_FILE = "profiles.json"
OPTIONS_FILE = "options.json"
RULES_FILE = "rules.json"
STATE_FILE = "state.json"
NETWORKS_FILE = "networks.json"
VERSION = 1
MAX_NETWORKS = 256
MAX_SSID_BYTES = 32  # IEEE 802.11 limit
MAX_FILE_BYTES = 256 * 1024
MAX_ROUTERS = 4  # routers remembered per network (a replaced one, a second access point...)
MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def validate_mac(mac: str) -> str:
    """A router's hardware address, lower case with colons; anything else is refused."""
    if not isinstance(mac, str) or not MAC_RE.fullmatch(mac) or mac == "00:00:00:00:00:00":
        raise ValidationError("invalid hardware address", code="invalid_network")
    return mac


@dataclass(frozen=True)
class Network:
    """A NetworkManager connection, identified by SSID and/or connection UUID."""

    ssid: str
    uuid: str
    profile: str
    # The routers seen on this network (their hardware addresses). A Wi-Fi with the same name
    # and password but another router is not trusted with this profile.
    routers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_name(self.profile, "profile")
        if not isinstance(self.routers, tuple) or len(self.routers) > MAX_ROUTERS:
            raise ValidationError("too many routers", code="invalid_network")
        for mac in self.routers:
            validate_mac(mac)
        if not self.ssid and not self.uuid:
            raise ValidationError("network needs an SSID or a UUID", code="invalid_network")
        if self.ssid:
            if not self.ssid.isprintable() or len(self.ssid.encode("utf-8")) > MAX_SSID_BYTES:
                raise ValidationError("invalid SSID", code="invalid_network")
        if self.uuid:
            try:
                canonical = str(uuid_mod.UUID(self.uuid))
            except ValueError as exc:
                raise ValidationError("invalid UUID", code="invalid_network") from exc
            if canonical != self.uuid:  # canonical lowercase form only
                raise ValidationError("invalid UUID", code="invalid_network")

    def same_network(self, ssid: str, uuid: str) -> bool:
        return self.uuid == uuid if self.uuid and uuid else bool(self.ssid) and self.ssid == ssid


def find_network(networks: list[Network], ssid: str, uuid: str) -> Network | None:
    """UUID match wins over SSID match."""
    if uuid:
        for n in networks:
            if n.uuid == uuid:
                return n
    if ssid:
        for n in networks:
            if n.ssid == ssid and not (n.uuid and uuid):
                return n
    return None


def find_profile(networks: list[Network], ssid: str, uuid: str) -> str | None:
    net = find_network(networks, ssid, uuid)
    return net.profile if net else None


def _jsonable(params) -> dict:
    return {k: list(v) if isinstance(v, (tuple, list)) else v for k, v in params.items()}


def _current_params(raw) -> dict:
    """Rule parameters as saved, with legacy profile ids in a "profiles" list renamed."""
    params = dict(raw)
    if isinstance(params.get("profiles"), list):
        params["profiles"] = [current_name(p) for p in params["profiles"]]
    return params


class Store:
    def __init__(self, directory: Path) -> None:
        self._dir = Path(directory)

    def _read(self, name: str) -> dict | None:
        path = self._dir / name
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                raise StoreCorrupt(f"{name} is too large")
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:  # includes JSON and unicode errors
            raise StoreCorrupt(f"cannot read {name}") from exc
        if not isinstance(data, dict) or data.get("version") != VERSION:
            raise StoreCorrupt(f"{name} has an unexpected format")
        return data

    def _write(self, name: str, data: dict) -> None:
        data = {"version": VERSION, **data}
        tmp = self._dir / f".{name}.tmp"
        try:
            # Only root needs these; the list of known Wi-Fi networks is nobody else's business.
            self._dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.fchmod(fd, 0o600)  # a leftover temp file keeps its old mode otherwise
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._dir / name)
            dfd = os.open(self._dir, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError as exc:
            raise StoreError(f"cannot write {name}: {exc.strerror}") from exc

    # -- last confirmed profile ---

    def load_state(self) -> str | None:
        """Profile name, or ``None`` when no state was ever saved. Raises StoreCorrupt."""
        data = self._read(STATE_FILE)
        if data is None:
            return None
        try:
            return validate_name(current_name(data.get("profile")), "profile")
        except ValidationError as exc:
            raise StoreCorrupt("state.json holds an invalid profile") from exc

    def save_state(self, profile: str) -> None:
        self._write(STATE_FILE, {"profile": validate_name(profile, "profile")})

    def quarantine_state(self) -> None:
        """Keep a damaged file for diagnosis instead of deleting it."""
        try:
            os.replace(self._dir / STATE_FILE, self._dir / (STATE_FILE + ".corrupt"))
        except OSError:
            pass

    # -- did fwmanager turn ufw on? (so uninstall can turn it off again) ---

    _UFW_MARKER = "ufw-enabled-by-fwmanager"

    def mark_ufw_enabled_by_us(self) -> None:
        self._write(self._UFW_MARKER, {"enabled": True})

    def ufw_enabled_by_us(self) -> bool:
        try:
            return (self._dir / self._UFW_MARKER).exists()
        except OSError:
            return False

    def clear_ufw_marker(self) -> None:
        (self._dir / self._UFW_MARKER).unlink(missing_ok=True)

    # -- edited profiles ---

    def load_profiles(self) -> dict[str, Profile]:
        """Profiles the user customised; absent names fall back to the built-in ones."""
        data = self._read(PROFILES_FILE)
        if data is None:
            return {}
        raw = data.get("profiles")
        if not isinstance(raw, dict) or len(raw) > 16:
            raise StoreCorrupt("profiles.json is malformed")
        try:
            return {
                current_name(name): Profile(
                    current_name(name),
                    tuple(p["services"]),
                    bool(p["ping"]),
                    bool(p["mdns"]),
                    Source(p["source"]["kind"], tuple(p["source"].get("cidrs", ()))),
                )
                for name, p in raw.items()
            }
        except (ValidationError, KeyError, TypeError, AttributeError) as exc:
            raise StoreCorrupt("profiles.json holds an invalid profile") from exc

    def save_profiles(self, profiles: dict[str, Profile]) -> None:
        self._write(
            PROFILES_FILE,
            {
                "profiles": {
                    n: {
                        "services": list(p.services),
                        "ping": p.ping,
                        "mdns": p.mdns,
                        "source": {"kind": p.source.kind, "cidrs": list(p.source.cidrs)},
                    }
                    for n, p in profiles.items()
                }
            },
        )

    # -- plain-language rules ---

    def load_rules(self) -> list[Rule]:
        data = self._read(RULES_FILE)
        if data is None:
            return []
        items = data.get("rules")
        if not isinstance(items, list) or len(items) > MAX_RULES:
            raise StoreCorrupt("rules.json is malformed")
        try:
            return [Rule(i["id"], i["template"], _current_params(i["params"])) for i in items]
        except (ValidationError, KeyError, TypeError, ValueError) as exc:
            raise StoreCorrupt("rules.json holds an invalid rule") from exc

    def save_rules(self, rules: list[Rule]) -> None:
        if len(rules) > MAX_RULES:
            raise ValidationError("too many rules", code="too_many_rules")
        self._write(
            RULES_FILE,
            {
                "rules": [
                    {"id": r.id, "template": r.template, "params": _jsonable(r.params)}
                    for r in rules
                ]
            },
        )

    # -- daemon-wide options ---

    def load_options(self) -> Options:
        data = self._read(OPTIONS_FILE)
        if data is None:
            return Options()
        try:
            level = data.get("log_level")
            return Options(router_mode=bool(data.get("router_mode", False)), log_level=level)
        except ValidationError as exc:
            raise StoreCorrupt("options.json holds an invalid value") from exc

    def save_options(self, options: Options) -> None:
        self._write(
            OPTIONS_FILE, {"router_mode": options.router_mode, "log_level": options.log_level}
        )

    # -- the user's own ufw default policies (restored on uninstall) ---

    _DEFAULTS_FILE = "ufw-original-defaults.json"
    _DIRECTIONS = ("incoming", "outgoing", "routed")
    _WORDS = ("allow", "deny", "reject")

    def save_original_defaults_once(self, defaults: dict[str, str]) -> None:
        if (self._dir / self._DEFAULTS_FILE).exists():
            return  # already remembered: later changes are ours, not the user's
        clean = {d: v for d, v in defaults.items() if d in self._DIRECTIONS and v in self._WORDS}
        self._write(self._DEFAULTS_FILE, {"defaults": clean})

    def load_original_defaults(self) -> dict[str, str]:
        try:
            data = self._read(self._DEFAULTS_FILE)
        except StoreCorrupt:
            return {}
        raw = (data or {}).get("defaults", {})
        if not isinstance(raw, dict):
            return {}
        return {d: v for d, v in raw.items() if d in self._DIRECTIONS and v in self._WORDS}

    def clear_original_defaults(self) -> None:
        (self._dir / self._DEFAULTS_FILE).unlink(missing_ok=True)

    # -- network -> profile mapping ---

    def load_networks(self) -> list[Network]:
        data = self._read(NETWORKS_FILE)
        if data is None:
            return []
        items = data.get("networks")
        if not isinstance(items, list) or len(items) > MAX_NETWORKS:
            raise StoreCorrupt("networks.json is malformed")
        try:
            return [
                Network(
                    str(i.get("ssid", "")),
                    str(i.get("uuid", "")),
                    current_name(i.get("profile")),
                    tuple(str(m) for m in i.get("routers", ())),
                )
                for i in items
            ]
        except (ValidationError, AttributeError, TypeError) as exc:
            raise StoreCorrupt("networks.json holds an invalid entry") from exc

    def save_networks(self, networks: list[Network]) -> None:
        if len(networks) > MAX_NETWORKS:
            raise ValidationError("too many networks", code="too_many_networks")
        self._write(
            NETWORKS_FILE,
            {
                "networks": [
                    {
                        "ssid": n.ssid,
                        "uuid": n.uuid,
                        "profile": n.profile,
                        "routers": list(n.routers),
                    }
                    for n in networks
                ]
            },
        )
