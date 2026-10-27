"""Detect another firewall manager (blocking) and container/VM networking (warning only).

ufw itself is *not* a conflict: it is the engine we drive.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from gi.repository import Gio, GLib

BLOCKING_UNITS = {"firewalld": "firewalld.service"}
WARNING_UNITS = {"docker": "docker.service", "libvirt": "libvirtd.service"}


@dataclass(frozen=True)
class Conflicts:
    blocking: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class SystemdUnits:
    """Asks systemd (over D-Bus, no subprocess) whether a unit is active."""

    def __init__(self, conn: Gio.DBusConnection) -> None:
        self._conn = conn

    def _call(self, path: str, iface: str, method: str, args: GLib.Variant, reply: str):
        return self._conn.call_sync(
            "org.freedesktop.systemd1", path, iface, method, args,
            GLib.VariantType(reply), Gio.DBusCallFlags.NONE, 2000, None,
        ).unpack()[0]  # fmt: skip

    def is_active(self, unit: str) -> bool:
        try:
            path = self._call(
                "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager", "GetUnit",
                GLib.Variant("(s)", (unit,)), "(o)",
            )  # fmt: skip
            state = self._call(
                path, "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", ("org.freedesktop.systemd1.Unit", "ActiveState")), "(v)",
            )  # fmt: skip
        except GLib.Error:
            return False  # unit not loaded, or systemd not reachable: not active
        return state in ("active", "activating", "reloading")


def detect(is_active: Callable[[str], bool]) -> Conflicts:
    blocking = tuple(n for n, unit in BLOCKING_UNITS.items() if is_active(unit))
    warnings = tuple(sorted(n for n, unit in WARNING_UNITS.items() if is_active(unit)))
    return Conflicts(blocking, warnings)
