"""D-Bus interface definition for ``org.fwmanager.Control``."""

from __future__ import annotations

from gi.repository import Gio

BUS_NAME = "org.fwmanager.Control"
OBJECT_PATH = "/org/fwmanager/Control"
INTERFACE = "org.fwmanager.Control"

INTROSPECTION_XML = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="ApplyProfile">
      <arg type="s" name="profile" direction="in"/>
    </method>
    <method name="Confirm"/>
    <method name="Rollback"/>
    <method name="GetStatus">
      <arg type="a{{sv}}" name="status" direction="out"/>
    </method>
    <method name="ListProfiles">
      <arg type="as" name="profiles" direction="out"/>
    </method>
    <signal name="Applied">
      <arg type="s" name="profile"/>
      <arg type="u" name="timeout"/>
    </signal>
    <signal name="Confirmed">
      <arg type="s" name="profile"/>
    </signal>
    <signal name="Reverted">
      <arg type="s" name="profile"/>
    </signal>
  </interface>
</node>
"""


def interface_info() -> Gio.DBusInterfaceInfo:
    return Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML).lookup_interface(INTERFACE)
