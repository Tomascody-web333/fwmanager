"""The D-Bus facing service. Every call is authorized, validated, then dispatched.

Clients can only invoke fixed commands; no rule text is ever accepted.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from gi.repository import Gio, GLib

from fwmanager.core.manager import CONFIRM_TIMEOUT, FirewallManager
from fwmanager.core.profiles import Profile
from fwmanager.core.validate import validate_name
from fwmanager.daemon.interface import INTERFACE, OBJECT_PATH, interface_info
from fwmanager.errors import AccessDenied, FwError, ValidationError, dbus_error_name

log = logging.getLogger("fwmanager")


class ControlService:
    def __init__(
        self,
        manager: FirewallManager,
        profiles: dict[str, Profile],
        authorize: Callable[[int], bool],
        confirm_timeout: int = CONFIRM_TIMEOUT,
    ) -> None:
        self._timeout = confirm_timeout
        self._mgr = manager
        self._profiles = profiles
        self._authorize = authorize
        self._conn: Gio.DBusConnection | None = None
        manager.on_revert = lambda name: self._emit("Reverted", "(s)", (name,))

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
            profile = self._profiles.get(name)
            if profile is None:
                raise ValidationError("unknown profile", code="unknown_profile")
            self._mgr.apply(profile, timeout=self._timeout)
            self._emit("Applied", "(su)", (name, self._timeout))
            return None
        if method == "Confirm":
            name = self._mgr.pending_profile()
            self._mgr.confirm()
            self._emit("Confirmed", "(s)", (name,))
            return None
        if method == "Rollback":
            self._mgr.rollback()
            return None
        if method == "GetStatus":
            st = self._mgr.status()
            return GLib.Variant(
                "(a{sv})",
                (
                    {
                        "table_present": GLib.Variant("b", st["table_present"]),
                        "active_profile": GLib.Variant("s", st["active_profile"] or ""),
                        "pending_profile": GLib.Variant("s", st["pending_profile"] or ""),
                        "seconds_left": GLib.Variant("u", st["seconds_left"]),
                    },
                ),
            )
        if method == "ListProfiles":
            return GLib.Variant("(as)", (sorted(self._profiles),))
        raise ValidationError("unknown method", code="invalid_input")

    def shutdown(self) -> None:
        """Never leave an unconfirmable change behind."""
        if self._mgr.pending_profile() is not None:
            try:
                self._mgr.rollback()
            except FwError:
                pass
