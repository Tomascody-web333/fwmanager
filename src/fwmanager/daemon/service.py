"""The D-Bus facing service. Every call is authorized, validated, then dispatched.

Clients can only invoke fixed commands; no rule text is ever accepted.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable

from gi.repository import Gio, GLib

from fwmanager.core import blocklog
from fwmanager.core.backend import Options
from fwmanager.core.conflicts import Conflicts
from fwmanager.core.manager import CONFIRM_TIMEOUT, FirewallManager
from fwmanager.core.policy_check import check_profile
from fwmanager.core.profiles import Profile, Source
from fwmanager.core.store import MAX_NETWORKS, Network, Store, find_profile
from fwmanager.core.validate import validate_name
from fwmanager.daemon.interface import INTERFACE, OBJECT_PATH, interface_info
from fwmanager.errors import AccessDenied, FwError, StoreError, ValidationError, dbus_error_name

log = logging.getLogger("fwmanager")


class ControlService:
    def __init__(
        self,
        manager: FirewallManager,
        profiles: dict[str, Profile],
        authorize: Callable[[int], bool],
        confirm_timeout: int = CONFIRM_TIMEOUT,
        store: Store | None = None,
        detect_conflicts: Callable[[], Conflicts] = Conflicts,
        startup_outcome: str = "ok",
    ) -> None:
        self._store = store
        self._detect = detect_conflicts
        self._startup = startup_outcome
        self._timeout = confirm_timeout
        self._mgr = manager
        self._defaults = dict(profiles)
        self._profiles = dict(profiles)
        self._options = Options()
        self._events: deque[tuple[int, str, str]] = deque(maxlen=200)
        self._authorize = authorize
        self._conn: Gio.DBusConnection | None = None
        self._load_saved_settings()
        self._record("startup", startup_outcome)
        manager.on_revert = self._on_reverted

    def _load_saved_settings(self) -> None:
        """Edited profiles and options; damaged files fall back to the built-in defaults."""
        if self._store is None:
            return
        try:
            self._profiles.update(self._store.load_profiles())
        except FwError:
            log.exception("saved profiles are damaged; using the built-in ones")
        try:
            self._options = self._store.load_options()
        except FwError:
            log.exception("saved options are damaged; using the defaults")

    def _record(self, kind: str, detail: str = "") -> None:
        self._events.append((int(time.time()), kind, detail))

    def _on_reverted(self, name: str) -> None:
        self._record("reverted", name)
        self._emit("Reverted", "(s)", (name,))

    def register(self, conn: Gio.DBusConnection) -> None:
        self._conn = conn
        conn.register_object(OBJECT_PATH, interface_info(), self._on_method_call, None, None)

    def _emit(self, signal: str, sig: str, args: tuple) -> None:
        if self._conn is not None:
            self._conn.emit_signal(None, OBJECT_PATH, INTERFACE, signal, GLib.Variant(sig, args))

    def _caller_uid(self, conn: Gio.DBusConnection, sender: str) -> int:
        reply = conn.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "GetConnectionUnixUser",
            GLib.Variant("(s)", (sender,)),
            GLib.VariantType("(u)"),
            Gio.DBusCallFlags.NONE,
            2000,
            None,
        )
        return reply.unpack()[0]

    def _on_method_call(self, conn, sender, _path, _iface, method, params, invocation) -> None:
        try:
            try:
                uid = self._caller_uid(conn, sender)
            except GLib.Error as exc:
                raise AccessDenied("cannot identify caller") from exc
            if not self._authorize(uid):
                log.warning("denied %s from uid %d", method, uid)
                self._record("denied", "")
                raise AccessDenied("caller is not in the fwmanager group")
            log.info("call %s from uid %d", method, uid)
            invocation.return_value(self.dispatch(method, params.unpack()))
        except FwError as exc:
            log.info("call %s failed: %s", method, exc.code)
            invocation.return_dbus_error(dbus_error_name(exc.code), exc.message)
        except Exception:
            log.exception("unexpected error in %s", method)
            invocation.return_dbus_error(dbus_error_name("internal_error"), "internal error")

    def dispatch(self, method: str, args: tuple) -> GLib.Variant | None:
        """Run one command; returns the reply variant (or None for no output)."""
        if method == "ApplyProfile":
            name = validate_name(args[0], "profile")
            if self._detect().blocking:
                raise FwError("another firewall is active", code="firewall_conflict")
            profile = self._profiles.get(name)
            if profile is None:
                raise ValidationError("unknown profile", code="unknown_profile")
            self._mgr.apply(profile, self._options, timeout=self._timeout)
            self._record("applied", name)
            self._emit("Applied", "(su)", (name, self._timeout))
            return None
        if method == "Confirm":
            name = self._mgr.pending_profile()
            self._mgr.confirm()
            self._persist_confirmed(name)
            self._record("confirmed", name or "")
            self._emit("Confirmed", "(s)", (name,))
            return None
        if method == "Rollback":
            self._mgr.rollback()
            return None
        if method == "GetStatus":
            st = self._mgr.status()
            conflicts = self._detect()
            return GLib.Variant(
                "(a{sv})",
                (
                    {
                        "ufw_installed": GLib.Variant("b", st["ufw_installed"]),
                        "firewall_active": GLib.Variant("b", st["firewall_active"]),
                        "active_profile": GLib.Variant("s", st["active_profile"] or ""),
                        "pending_profile": GLib.Variant("s", st["pending_profile"] or ""),
                        "seconds_left": GLib.Variant("u", st["seconds_left"]),
                        "blocking_conflicts": GLib.Variant("as", list(conflicts.blocking)),
                        "warnings": GLib.Variant("as", list(conflicts.warnings)),
                        "startup": GLib.Variant("s", self._startup),
                    },
                ),
            )
        if method == "ListProfiles":
            return GLib.Variant("(as)", (sorted(self._profiles),))
        if method == "CheckProfile":
            name = validate_name(args[0], "profile")
            profile = self._profiles.get(name)
            if profile is None:
                raise ValidationError("unknown profile", code="unknown_profile")
            report = check_profile(
                profile, self._mgr.backend, self._options, self._detect()
            )  # read-only: nothing is applied
            return GLib.Variant(
                "(a(sss))", ([(f.severity, f.code, f.detail) for f in report.findings],)
            )
        if method == "GetProfile":
            return GLib.Variant("(a{sv})", (self._profile_variant(self._get_profile(args[0])),))
        if method == "SetProfile":
            return self._set_profile(*args)
        if method == "ResetProfile":
            name = self._get_profile(args[0]).name
            self._profiles[name] = self._defaults[name]
            self._save_profiles()
            return None
        if method == "ListServices":
            items = sorted((s.id, s.title) for s in self._mgr.catalog.values())
            return GLib.Variant("(a(ss))", (items,))
        if method == "GetOptions":
            o = self._options
            return GLib.Variant(
                "(a{sv})",
                (
                    {
                        "router_mode": GLib.Variant("b", o.router_mode),
                        "log_level": GLib.Variant("s", o.log_level or ""),
                    },
                ),
            )
        if method == "SetOptions":
            router_mode, log_level = args
            new = Options(router_mode=bool(router_mode), log_level=log_level or None)
            self._require_store().save_options(new)  # persisted first: never half-applied
            self._options = new
            self._record("options", "")
            return None
        if method == "GetEvents":
            return GLib.Variant("(a(tss))", (list(self._events),))
        if method == "GetRecentBlocked":
            items = blocklog.read_recent(args[0])
            return GLib.Variant(
                "(a(ssssss))",
                ([(b.when, b.iface, b.src, b.dst, b.proto, b.dport) for b in items],),
            )
        if method == "ListNetworks":
            nets = self._load_networks()
            return GLib.Variant("(a(sss))", ([(n.ssid, n.uuid, n.profile) for n in nets],))
        if method == "SetNetworkProfile":
            ssid, uuid, profile = args
            if profile not in self._profiles:
                validate_name(profile, "profile")
                raise ValidationError("unknown profile", code="unknown_profile")
            new = Network(ssid, uuid, profile)  # validates
            nets = [n for n in self._load_networks() if not n.same_network(ssid, uuid)]
            if len(nets) >= MAX_NETWORKS:
                raise ValidationError("too many networks", code="too_many_networks")
            self._save_networks([*nets, new])
            return None
        if method == "RemoveNetwork":
            ssid, uuid = args
            Network(ssid, uuid, next(iter(self._profiles)))  # validates ssid/uuid only
            nets = self._load_networks()
            self._save_networks([n for n in nets if not n.same_network(ssid, uuid)])
            return None
        if method == "GetNetworkProfile":
            ssid, uuid = args
            Network(ssid, uuid, next(iter(self._profiles)))
            return GLib.Variant("(s)", (find_profile(self._load_networks(), ssid, uuid) or "",))
        raise ValidationError("unknown method", code="invalid_input")

    def _get_profile(self, name: str) -> Profile:
        name = validate_name(name, "profile")
        profile = self._profiles.get(name)
        if profile is None:
            raise ValidationError("unknown profile", code="unknown_profile")
        return profile

    def _profile_variant(self, p: Profile) -> dict:
        return {
            "name": GLib.Variant("s", p.name),
            "services": GLib.Variant("as", list(p.services)),
            "ping": GLib.Variant("b", p.ping),
            "mdns": GLib.Variant("b", p.mdns),
            "source_kind": GLib.Variant("s", p.source.kind),
            "cidrs": GLib.Variant("as", list(p.source.cidrs)),
            "customized": GLib.Variant("b", p != self._defaults[p.name]),
        }

    def _set_profile(self, name, services, ping, mdns, kind, cidrs) -> None:
        current = self._get_profile(name)  # only the built-in profiles can be edited
        catalog = self._mgr.catalog
        if len(services) > len(catalog):
            raise ValidationError("too many services", code="invalid_service")
        for sid in services:
            validate_name(sid, "service")
            if sid not in catalog:
                raise ValidationError("unknown service", code="unknown_service")
        new = Profile(
            current.name, tuple(services), bool(ping), bool(mdns), Source(kind, tuple(cidrs))
        )
        self._profiles[new.name] = new
        try:
            self._save_profiles()
        except FwError:
            self._profiles[new.name] = current  # keep memory and disk in step
            raise
        self._record("profile_edited", new.name)

    def _save_profiles(self) -> None:
        custom = {n: p for n, p in self._profiles.items() if p != self._defaults[n]}
        self._require_store().save_profiles(custom)

    def _require_store(self) -> Store:
        if self._store is None:
            raise StoreError("persistence is not configured")
        return self._store

    def _load_networks(self) -> list[Network]:
        return self._require_store().load_networks()

    def _save_networks(self, nets: list[Network]) -> None:
        self._require_store().save_networks(nets)

    def _persist_confirmed(self, name: str | None) -> None:
        """Remember the confirmed profile; a disk problem must not undo the confirmation."""
        if self._store is None or name is None:
            return
        try:
            self._store.save_state(name)
        except FwError:
            log.exception("could not save the confirmed profile")

    def shutdown(self) -> None:
        """Never leave an unconfirmable change behind."""
        if self._mgr.pending_profile() is not None:
            try:
                self._mgr.rollback()
            except FwError:
                pass
