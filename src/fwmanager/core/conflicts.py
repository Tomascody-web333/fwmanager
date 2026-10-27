"""Detect other firewalls (blocking) and container/VM networking (warning only)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from gi.repository import Gio, GLib

from fwmanager.core.nft import Nft
from fwmanager.errors import FwError

log = logging.getLogger("fwmanager")

BLOCKING_UNITS = {"ufw": "ufw.service", "firewalld": "firewalld.service"}
WARNING_UNITS = {"docker": "docker.service", "libvirt": "libvirtd.service"}
UFW_CONF = Path("/etc/ufw/ufw.conf")


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


def ufw_enabled(conf: Path = UFW_CONF) -> bool:
    """ufw.service stays 'active' even when ufw is disabled; ufw.conf has the truth."""
    try:
        for line in conf.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "ENABLED":
                return value.strip().lower() == "yes"
    except OSError:
        pass
    return True  # cannot tell: be cautious and treat an active unit as enabled


def _foreign_table_hints(nft: Nft | None) -> set[str]:
    if nft is None:
        return set()
    try:
        names = [line.split()[-1] for line in nft.run(["list", "tables"]).splitlines() if line]
    except (FwError, IndexError):
        return set()
    hints = set()
    for name in names:
        if "docker" in name:
            hints.add("docker")
        if name.startswith("libvirt"):
            hints.add("libvirt")
    return hints


def detect(
    is_active: Callable[[str], bool],
    nft: Nft | None = None,
    ufw_conf: Path = UFW_CONF,
) -> Conflicts:
    blocking = []
    for name, unit in BLOCKING_UNITS.items():
        if is_active(unit) and (name != "ufw" or ufw_enabled(ufw_conf)):
            blocking.append(name)
    warnings = {n for n, unit in WARNING_UNITS.items() if is_active(unit)}
    warnings |= _foreign_table_hints(nft)
    return Conflicts(tuple(blocking), tuple(sorted(warnings)))
