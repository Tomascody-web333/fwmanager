"""What first-run setup still needs, decided without any GTK.

Each requirement is one row of the wizard: its state, and what the person can do about it.
"""

from __future__ import annotations

import grp
import os
import pwd
import shutil
from collections.abc import Callable
from dataclasses import dataclass

from fwmanager.i18n import N_, _
from fwmanager.ui import state

# Row states
OK, MISSING, RESTART, UNKNOWN = "ok", "missing", "restart", "unknown"

# What can be done about a row
FIX_HELPER = "helper"  # one administrator confirmation via polkit
FIX_RESTART = "restart"  # start the app again with the new group
FIX_COMMAND = "command"  # show a command to copy
FIX_NONE = "none"

SERVICE, ACCOUNT, UFW, CONFLICT = "service", "account", "ufw", "conflict"
ORDER = (SERVICE, ACCOUNT, UFW, CONFLICT)
TITLES = {
    SERVICE: N_("The background service is running"),
    ACCOUNT: N_("This account may use fwmanager"),
    UFW: N_("ufw is installed"),
    CONFLICT: N_("No other firewall manager is in the way"),
}


@dataclass(frozen=True)
class Requirement:
    id: str
    state: str
    fix: str = FIX_NONE
    command: str | None = None

    @property
    def title(self) -> str:
        return _(TITLES[self.id])

    @property
    def ok(self) -> bool:
        return self.state == OK


@dataclass(frozen=True)
class GroupInfo:
    """Where the account stands with the ``fwmanager`` group."""

    exists: bool
    listed: bool  # recorded in /etc/group (or as the primary group)
    held: bool  # this very process has the group


def group_info(
    name: str = "fwmanager",
    user: str | None = None,
    getgrnam: Callable = grp.getgrnam,
    getpwnam: Callable = pwd.getpwnam,
    getgroups: Callable = os.getgroups,
    getegid: Callable = os.getegid,
) -> GroupInfo:
    user = user or pwd.getpwuid(os.getuid()).pw_name
    try:
        g = getgrnam(name)
    except KeyError:
        return GroupInfo(False, False, False)
    try:
        primary = getpwnam(user).pw_gid
    except KeyError:
        primary = -1
    listed = user in g.gr_mem or primary == g.gr_gid
    held = g.gr_gid in getgroups() or g.gr_gid == getegid()
    return GroupInfo(True, listed, held)


def evaluate(
    snapshot_screen: str,
    status: dict | None,
    group: GroupInfo,
    ufw_present: bool,
    user: str,
) -> list[Requirement]:
    """The four rows, in the order the wizard shows them."""
    service_up = snapshot_screen not in (state.DAEMON_DOWN,)
    service = (
        Requirement(SERVICE, OK)
        if service_up
        else Requirement(SERVICE, MISSING, FIX_HELPER, "sudo systemctl enable --now fwmanagerd")
    )

    if group.held and snapshot_screen != state.NOT_IN_GROUP:
        account = Requirement(ACCOUNT, OK)
    elif group.listed and not group.held:
        account = Requirement(ACCOUNT, RESTART, FIX_RESTART)
    else:
        account = Requirement(ACCOUNT, MISSING, FIX_HELPER, f"sudo usermod -aG fwmanager {user}")

    ufw_ok = status["ufw_installed"] if status else ufw_present
    ufw = (
        Requirement(UFW, OK)
        if ufw_ok
        else Requirement(UFW, MISSING, FIX_COMMAND, "sudo apt install ufw")
    )

    blockers = (status or {}).get("blocking_conflicts", [])
    if blockers:
        conflict = Requirement(
            CONFLICT, MISSING, FIX_COMMAND, f"sudo systemctl disable --now {blockers[0]}.service"
        )
    elif status is None:
        conflict = Requirement(CONFLICT, UNKNOWN)
    else:
        conflict = Requirement(CONFLICT, OK)
    return [service, account, ufw, conflict]


def ready(requirements: list[Requirement]) -> bool:
    """Everything is in place; `unknown` only counts while the service itself is not answering."""
    return all(r.ok for r in requirements)


def ufw_on_path() -> bool:
    return shutil.which("ufw") is not None
