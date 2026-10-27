"""Restart the app with the group it was just given.

dbus-daemon judges a ``<policy group>`` by the groups the *process* really has. A user added to
the ``fwmanager`` group a moment ago is therefore still refused until the app is started afresh;
``sg`` starts it with that group, so setup can carry on without logging out and in again.
"""

from __future__ import annotations

import os
import shlex
import shutil
import sys

GROUP = "fwmanager"


def relaunch_command(exe: str, group: str = GROUP, sg: str | None = None) -> list[str] | None:
    """The argument list for ``sg``, or ``None`` if ``sg`` is not available."""
    sg = sg or shutil.which("sg")
    if not sg:
        return None
    return [sg, group, "-c", shlex.join([exe])]


def current_executable() -> str:
    """What to start again: the installed ``fwmanager`` launcher if there is one."""
    return shutil.which("fwmanager") or sys.argv[0]


def relaunch(exe: str | None = None, execv=os.execv) -> bool:
    """Replace this process. Returns ``False`` (and does nothing) if it cannot be done."""
    command = relaunch_command(exe or current_executable())
    if command is None:
        return False
    execv(command[0], command)
    return True  # only reached by a fake execv in tests
