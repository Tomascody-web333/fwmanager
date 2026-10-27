"""The connection the machine is using right now, asked of NetworkManager over D-Bus."""

from __future__ import annotations

from dataclasses import dataclass

from gi.repository import Gio, GLib

NM = "org.freedesktop.NetworkManager"
PROPS = "org.freedesktop.DBus.Properties"
WIFI = "802-11-wireless"
WIFI_SECURITY = "802-11-wireless-security"
# Only these need a password (or an account) to join. "none" is WEP, broken for decades, and
# "owe" (Enhanced Open) is encrypted but lets anyone in.
PASSWORD_KEY_MGMT = ("wpa-psk", "sae", "wpa-eap", "wpa-eap-suite-b-192")


@dataclass(frozen=True)
class CurrentNetwork:
    name: str  # NetworkManager's connection name
    ssid: str  # empty unless Wi-Fi
    uuid: str
    kind: str  # e.g. "802-11-wireless", "802-3-ethernet"
    open: bool = False  # a Wi-Fi anyone nearby can join (no password)

    @property
    def is_wifi(self) -> bool:
        return self.kind == WIFI


def _connect(bus_address: str | None) -> Gio.DBusConnection:
    if bus_address:
        return Gio.DBusConnection.new_for_address_sync(
            bus_address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None,
            None,
        )
    return Gio.bus_get_sync(Gio.BusType.SYSTEM, None)


def _get(conn, path: str, iface: str, prop: str):
    reply = conn.call_sync(
        NM, path, PROPS, "Get", GLib.Variant("(ss)", (iface, prop)),
        GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 2000, None,
    )  # fmt: skip
    return reply.unpack()[0]


def current_network(bus_address: str | None = None) -> CurrentNetwork | None:
    """Blocking; call it from a worker. ``None`` when offline or NetworkManager is absent."""
    try:
        conn = _connect(bus_address)
        active = _get(conn, "/org/freedesktop/NetworkManager", NM, "PrimaryConnection")
        if not active or active == "/":
            return None
        ac = "org.freedesktop.NetworkManager.Connection.Active"
        name = str(_get(conn, active, ac, "Id"))
        uuid = str(_get(conn, active, ac, "Uuid"))
        kind = str(_get(conn, active, ac, "Type"))
        ssid, is_open = "", False
        if kind == WIFI:
            settings_path = _get(conn, active, ac, "Connection")
            reply = conn.call_sync(
                NM, settings_path, "org.freedesktop.NetworkManager.Settings.Connection",
                "GetSettings", None, GLib.VariantType("(a{sa{sv}})"),
                Gio.DBusCallFlags.NONE, 2000, None,
            )  # fmt: skip
            settings = reply.unpack()[0]
            raw = settings.get(WIFI, {}).get("ssid", b"")
            ssid = bytes(raw).decode("utf-8", errors="replace")
            key_mgmt = settings.get(WIFI_SECURITY, {}).get("key-mgmt", "")
            is_open = key_mgmt not in PASSWORD_KEY_MGMT
    except GLib.Error:
        return None
    return CurrentNetwork(name, ssid, uuid, kind, is_open)
