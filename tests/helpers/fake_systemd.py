"""A minimal fake of org.freedesktop.systemd1 for tests on a private bus.

Usage: python fake_systemd.py BUS_ADDRESS unit1.service [unit2.service ...]
The listed units report ActiveState=active; any other unit is "not loaded".
"""

import sys

from gi.repository import Gio, GLib

XML = """
<node>
  <interface name="org.freedesktop.systemd1.Manager">
    <method name="GetUnit"><arg type="s" direction="in"/><arg type="o" direction="out"/></method>
  </interface>
  <interface name="org.freedesktop.DBus.Properties">
    <method name="Get">
      <arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/>
    </method>
  </interface>
</node>
"""


def main() -> None:
    address, active = sys.argv[1], set(sys.argv[2:])
    conn = Gio.DBusConnection.new_for_address_sync(
        address,
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None,
        None,
    )
    node = Gio.DBusNodeInfo.new_for_xml(XML)

    def path_for(unit: str) -> str:
        return "/unit/" + unit.replace(".", "_")

    def on_call(_c, _s, _p, _i, method, params, inv):
        if method == "GetUnit":
            unit = params.unpack()[0]
            if unit in active:
                inv.return_value(GLib.Variant("(o)", (path_for(unit),)))
            else:
                inv.return_dbus_error("org.freedesktop.systemd1.NoSuchUnit", "not loaded")
        elif method == "Get":
            inv.return_value(GLib.Variant("(v)", (GLib.Variant("s", "active"),)))

    conn.register_object(
        "/org/freedesktop/systemd1",
        node.lookup_interface("org.freedesktop.systemd1.Manager"),
        on_call,
        None,
        None,
    )
    for unit in active:
        conn.register_object(
            path_for(unit),
            node.lookup_interface("org.freedesktop.DBus.Properties"),
            on_call,
            None,
            None,
        )
    Gio.bus_own_name_on_connection(
        conn, "org.freedesktop.systemd1", Gio.BusNameOwnerFlags.NONE, None, None
    )
    print("ready", flush=True)
    GLib.MainLoop().run()


main()
