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
    <method name="CheckProfile">
      <arg type="s" name="profile" direction="in"/>
      <arg type="a(sss)" name="findings" direction="out"/>
    </method>
    <method name="GetProfile">
      <arg type="s" name="profile" direction="in"/>
      <arg type="a{{sv}}" name="details" direction="out"/>
    </method>
    <method name="SetProfile">
      <arg type="s" name="profile" direction="in"/>
      <arg type="as" name="services" direction="in"/>
      <arg type="b" name="ping" direction="in"/>
      <arg type="b" name="mdns" direction="in"/>
      <arg type="s" name="source_kind" direction="in"/>
      <arg type="as" name="cidrs" direction="in"/>
    </method>
    <method name="ResetProfile">
      <arg type="s" name="profile" direction="in"/>
    </method>
    <method name="ListServices">
      <arg type="a(ss)" name="services" direction="out"/>
    </method>
    <method name="GetOptions">
      <arg type="a{{sv}}" name="options" direction="out"/>
    </method>
    <method name="SetOptions">
      <arg type="b" name="router_mode" direction="in"/>
      <arg type="s" name="log_level" direction="in"/>
    </method>
    <method name="GetEvents">
      <arg type="a(tss)" name="events" direction="out"/>
    </method>
    <method name="GetRecentBlocked">
      <arg type="u" name="limit" direction="in"/>
      <arg type="a(ssssss)" name="packets" direction="out"/>
    </method>
    <method name="GetRecentAllowed">
      <arg type="u" name="limit" direction="in"/>
      <arg type="a(ssssss)" name="packets" direction="out"/>
    </method>
    <method name="ListRules">
      <arg type="a(ssa{{sv}})" name="rules" direction="out"/>
    </method>
    <method name="PreviewRule">
      <arg type="s" name="template" direction="in"/>
      <arg type="a{{sv}}" name="params" direction="in"/>
      <arg type="a(sasassssas)" name="changes" direction="out"/>
    </method>
    <method name="AddRule">
      <arg type="s" name="template" direction="in"/>
      <arg type="a{{sv}}" name="params" direction="in"/>
      <arg type="s" name="id" direction="out"/>
    </method>
    <method name="RemoveRule">
      <arg type="s" name="id" direction="in"/>
    </method>
    <method name="ListNetworks">
      <arg type="a(sss)" name="networks" direction="out"/>
    </method>
    <method name="SetNetworkProfile">
      <arg type="s" name="ssid" direction="in"/>
      <arg type="s" name="uuid" direction="in"/>
      <arg type="s" name="profile" direction="in"/>
    </method>
    <method name="RemoveNetwork">
      <arg type="s" name="ssid" direction="in"/>
      <arg type="s" name="uuid" direction="in"/>
    </method>
    <method name="GetNetworkProfile">
      <arg type="s" name="ssid" direction="in"/>
      <arg type="s" name="uuid" direction="in"/>
      <arg type="s" name="profile" direction="out"/>
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
