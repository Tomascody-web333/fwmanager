"""A minimal fake of org.freedesktop.NetworkManager for tests on a private bus.

Usage: python fake_nm.py BUS_ADDRESS KIND NAME UUID [SSID [KEY_MGMT]]
KIND is "802-11-wireless" or "802-3-ethernet", or "none" for no primary connection.
KEY_MGMT is the Wi-Fi security ("wpa-psk", "sae", ...); without it the Wi-Fi is open.
"""

import sys

from gi.repository import Gio, GLib

XML = """
<node>
  <interface name="org.freedesktop.DBus.Properties">
    <method name="Get">
      <arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/>
    </method>
  </interface>
  <interface name="org.freedesktop.NetworkManager.Settings.Connection">
    <method name="GetSettings"><arg type="a{sa{sv}}" direction="out"/></method>
  </interface>
</node>
"""


def main() -> None:
    address, kind, name, uuid = sys.argv[1:5]
    ssid = sys.argv[5] if len(sys.argv) > 5 else ""
    key_mgmt = sys.argv[6] if len(sys.argv) > 6 else ""
    conn = Gio.DBusConnection.new_for_address_sync(
        address,
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None,
        None,
    )
    node = Gio.DBusNodeInfo.new_for_xml(XML)
    active = "/org/freedesktop/NetworkManager/ActiveConnection/1"
    settings = "/org/freedesktop/NetworkManager/Settings/1"

    def on_props(_c, _s, path, _i, method, params, inv):
        iface, prop = params.unpack()
        if path == "/org/freedesktop/NetworkManager":
            value = GLib.Variant("o", "/" if kind == "none" else active)
        else:
            value = {
                "Id": GLib.Variant("s", name),
                "Uuid": GLib.Variant("s", uuid),
                "Type": GLib.Variant("s", kind),
                "Connection": GLib.Variant("o", settings),
            }[prop]
        inv.return_value(GLib.Variant("(v)", (value,)))

    def on_settings(_c, _s, _p, _i, method, params, inv):
        body = {"connection": {"id": GLib.Variant("s", name)}}
        if kind == "802-11-wireless":
            body["802-11-wireless"] = {"ssid": GLib.Variant("ay", ssid.encode())}
            if key_mgmt:
                body["802-11-wireless-security"] = {"key-mgmt": GLib.Variant("s", key_mgmt)}
        inv.return_value(GLib.Variant("(a{sa{sv}})", (body,)))

    props = node.lookup_interface("org.freedesktop.DBus.Properties")
    for path in ("/org/freedesktop/NetworkManager", active):
        conn.register_object(path, props, on_props, None, None)
    conn.register_object(
        settings,
        node.lookup_interface("org.freedesktop.NetworkManager.Settings.Connection"),
        on_settings,
        None,
        None,
    )
    Gio.bus_own_name_on_connection(
        conn, "org.freedesktop.NetworkManager", Gio.BusNameOwnerFlags.NONE, None, None
    )
    print("ready", flush=True)
    GLib.MainLoop().run()


main()
