"""Synchronous D-Bus client for ``org.fwmanager.Control`` (used by the CLI and the UI)."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Gio, GLib

from fwmanager.daemon.interface import BUS_NAME, INTERFACE, OBJECT_PATH
from fwmanager.errors import FwError, code_from_dbus_error

CALL_TIMEOUT_MS = 20000
# Applying or checking a profile runs ufw once per rule; on a slow machine that takes a while.
FIREWALL_CALL_TIMEOUT_MS = 120000


def _strip_remote_prefix(message: str, remote: str | None) -> str:
    """GLib prepends ``GDBus.Error:<name>: `` to remote errors; keep only the daemon's text."""
    prefix = f"GDBus.Error:{remote}: "
    return message[len(prefix) :] if remote and message.startswith(prefix) else message


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

    def _call(
        self,
        method: str,
        args: GLib.Variant | None = None,
        reply: str | None = None,
        timeout_ms: int = CALL_TIMEOUT_MS,
    ):
        try:
            result = self._conn.call_sync(
                BUS_NAME,
                OBJECT_PATH,
                INTERFACE,
                method,
                args,
                GLib.VariantType(reply) if reply else None,
                Gio.DBusCallFlags.NONE,
                timeout_ms,
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
            raise FwError(_strip_remote_prefix(exc.message, remote), code=code) from exc
        return result.unpack() if result is not None else None

    def apply_profile(self, name: str) -> None:
        self._call(
            "ApplyProfile", GLib.Variant("(s)", (name,)), timeout_ms=FIREWALL_CALL_TIMEOUT_MS
        )

    def confirm(self) -> None:
        self._call("Confirm")

    def rollback(self) -> None:
        self._call("Rollback", timeout_ms=FIREWALL_CALL_TIMEOUT_MS)

    def status(self) -> dict:
        return self._call("GetStatus", reply="(a{sv})")[0]

    def list_profiles(self) -> list[str]:
        return self._call("ListProfiles", reply="(as)")[0]

    def check_profile(self, name: str) -> list[tuple[str, str, str]]:
        """Read-only policy check done by the daemon: (severity, code, detail) tuples."""
        args = GLib.Variant("(s)", (name,))
        return self._call("CheckProfile", args, "(a(sss))", FIREWALL_CALL_TIMEOUT_MS)[0]

    def get_profile(self, name: str) -> dict:
        return self._call("GetProfile", GLib.Variant("(s)", (name,)), "(a{sv})")[0]

    def set_profile(self, name, services, ping, mdns, source_kind, cidrs=()) -> None:
        self._call(
            "SetProfile",
            GLib.Variant(
                "(sasbbsas)", (name, list(services), ping, mdns, source_kind, list(cidrs))
            ),
        )

    def reset_profile(self, name: str) -> None:
        self._call("ResetProfile", GLib.Variant("(s)", (name,)))

    def list_services(self) -> list[tuple[str, str]]:
        return self._call("ListServices", reply="(a(ss))")[0]

    def get_options(self) -> dict:
        return self._call("GetOptions", reply="(a{sv})")[0]

    def set_options(self, router_mode: bool, log_level: str = "") -> None:
        self._call("SetOptions", GLib.Variant("(bs)", (router_mode, log_level)))

    def get_events(self) -> list[tuple[int, str, str]]:
        return self._call("GetEvents", reply="(a(tss))")[0]

    def get_recent_blocked(self, limit: int = 50) -> list[tuple[str, ...]]:
        return self._call("GetRecentBlocked", GLib.Variant("(u)", (limit,)), "(a(ssssss))")[0]

    def get_recent_allowed(self, limit: int = 50) -> list[tuple[str, ...]]:
        return self._call("GetRecentAllowed", GLib.Variant("(u)", (limit,)), "(a(ssssss))")[0]

    @staticmethod
    def _params(params: dict) -> dict:
        return {
            k: GLib.Variant("s", v) if isinstance(v, str) else GLib.Variant("as", list(v))
            for k, v in params.items()
        }

    def list_rules(self) -> list[tuple[str, str, dict]]:
        return self._call("ListRules", reply="(a(ssa{sv}))")[0]

    def preview_rule(self, template: str, params: dict) -> list[tuple]:
        """Per profile that would change: (name, opened, closed, ping, mdns, source, cidrs)."""
        args = GLib.Variant("(sa{sv})", (template, self._params(params)))
        return self._call("PreviewRule", args, "(a(sasassssas))")[0]

    def add_rule(self, template: str, params: dict) -> str:
        args = GLib.Variant("(sa{sv})", (template, self._params(params)))
        return self._call("AddRule", args, "(s)")[0]

    def remove_rule(self, rule_id: str) -> None:
        self._call("RemoveRule", GLib.Variant("(s)", (rule_id,)))

    def list_networks(self) -> list[tuple[str, str, str]]:
        return self._call("ListNetworks", reply="(a(sss))")[0]

    def set_network_profile(self, ssid: str, uuid: str, profile: str) -> None:
        self._call("SetNetworkProfile", GLib.Variant("(sss)", (ssid, uuid, profile)))

    def remove_network(self, ssid: str, uuid: str) -> None:
        self._call("RemoveNetwork", GLib.Variant("(ss)", (ssid, uuid)))

    def get_network_profile(self, ssid: str, uuid: str) -> str:
        return self._call("GetNetworkProfile", GLib.Variant("(ss)", (ssid, uuid)), "(s)")[0]

    def check_network(self, ssid: str, uuid: str, router: str) -> tuple[str, str]:
        """(profile, verdict) for a network that just came up; see the service."""
        args = GLib.Variant("(sss)", (ssid, uuid, router))
        return tuple(self._call("CheckNetwork", args, "(ss)"))

    def list_network_routers(self) -> list[tuple[str, str, list[str], str]]:
        """(ssid, uuid, remembered routers, router seen but not recognised) per network."""
        return self._call("ListNetworkRouters", reply="(a(ssass))")[0]

    def trust_router(self, ssid: str, uuid: str) -> None:
        self._call("TrustRouter", GLib.Variant("(ss)", (ssid, uuid)))

    def forget_routers(self, ssid: str, uuid: str) -> None:
        self._call("ForgetRouters", GLib.Variant("(ss)", (ssid, uuid)))

    def export_settings(self) -> str:
        return self._call("ExportSettings", reply="(s)")[0]

    def import_settings(self, text: str) -> None:
        self._call("ImportSettings", GLib.Variant("(s)", (text,)))

    def subscribe(self, callback: Callable[[str, tuple], None]) -> int:
        """Call ``callback(signal_name, args)`` for every signal of the service (Applied,
        Confirmed, Reverted, RouterChanged)."""

        def _on_signal(_conn, _sender, _path, _iface, name, params) -> None:
            callback(name, params.unpack())

        return self._conn.signal_subscribe(
            BUS_NAME, INTERFACE, None, OBJECT_PATH, None, Gio.DBusSignalFlags.NONE, _on_signal
        )
