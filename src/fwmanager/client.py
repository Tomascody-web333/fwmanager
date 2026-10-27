"""Synchronous D-Bus client for ``org.fwmanager.Control`` (used by the CLI and the UI)."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Gio, GLib

from fwmanager.daemon.interface import BUS_NAME, INTERFACE, OBJECT_PATH
from fwmanager.errors import FwError, code_from_dbus_error

CALL_TIMEOUT_MS = 20000


class Client:
    def __init__(self, bus_address: str | None = None) -> None:
        try:
            if bus_address:
                self._conn = Gio.DBusConnection.new_for_address_sync(
                    bus_address,
                    Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                    | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                    None,
                    None,
                )
            else:
                self._conn = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except GLib.Error as exc:
            raise FwError(exc.message, code="daemon_unavailable") from exc

    def _call(self, method: str, args: GLib.Variant | None = None, reply: str | None = None):
        try:
            result = self._conn.call_sync(
                BUS_NAME,
                OBJECT_PATH,
                INTERFACE,
                method,
                args,
                GLib.VariantType(reply) if reply else None,
                Gio.DBusCallFlags.NONE,
                CALL_TIMEOUT_MS,
                None,
            )
        except GLib.Error as exc:
            remote = Gio.DBusError.get_remote_error(exc)
            if remote and remote.startswith("org.fwmanager.Error."):
                code = code_from_dbus_error(remote)
            elif remote == "org.freedesktop.DBus.Error.AccessDenied":
                code = "access_denied"
            else:
                code = "daemon_unavailable"
            raise FwError(Gio.DBusError.strip_remote_error(exc) or exc.message, code=code) from exc
        return result.unpack() if result is not None else None

    def apply_profile(self, name: str) -> None:
        self._call("ApplyProfile", GLib.Variant("(s)", (name,)))

    def confirm(self) -> None:
        self._call("Confirm")

    def rollback(self) -> None:
        self._call("Rollback")

    def status(self) -> dict:
        return self._call("GetStatus", reply="(a{sv})")[0]

    def list_profiles(self) -> list[str]:
        return self._call("ListProfiles", reply="(as)")[0]

    def list_networks(self) -> list[tuple[str, str, str]]:
        return self._call("ListNetworks", reply="(a(sss))")[0]

    def set_network_profile(self, ssid: str, uuid: str, profile: str) -> None:
        self._call("SetNetworkProfile", GLib.Variant("(sss)", (ssid, uuid, profile)))

    def remove_network(self, ssid: str, uuid: str) -> None:
        self._call("RemoveNetwork", GLib.Variant("(ss)", (ssid, uuid)))

    def get_network_profile(self, ssid: str, uuid: str) -> str:
        return self._call("GetNetworkProfile", GLib.Variant("(ss)", (ssid, uuid)), "(s)")[0]

    def subscribe(self, callback: Callable[[str, tuple], None]) -> int:
        """Call ``callback(signal_name, args)`` for Applied/Confirmed/Reverted signals."""

        def _on_signal(_conn, _sender, _path, _iface, name, params) -> None:
            callback(name, params.unpack())

        return self._conn.signal_subscribe(
            BUS_NAME, INTERFACE, None, OBJECT_PATH, None, Gio.DBusSignalFlags.NONE, _on_signal
        )
