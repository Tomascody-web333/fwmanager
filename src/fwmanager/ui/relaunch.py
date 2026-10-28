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
MARKER = "FWMANAGER_RELAUNCHED"  # set on the restarted process, so it never restarts again


def relaunch_command(
    exe: str, group: str = GROUP, sg: str | None = None, args: list[str] | None = None
) -> list[str] | None:
    """The argument list for ``sg``, or ``None`` if ``sg`` is not available."""
    sg = sg or shutil.which("sg")
    if not sg:
        return None
    return [sg, group, "-c", shlex.join([exe, *(args or [])])]


def current_executable(name: str = "fwmanager") -> str:
    """What to start again: the installed launcher if there is one."""
    return shutil.which(name) or sys.argv[0]


def already_relaunched(env=os.environ) -> bool:
    return env.get(MARKER) == "1"


def relaunch(
    exe: str | None = None, execv=os.execv, args: list[str] | None = None, env=os.environ
) -> bool:
    """Replace this process. Returns ``False`` (and does nothing) if it cannot be done."""
    if already_relaunched(env):
        return False  # the group did not take even after a restart: do not loop
    command = relaunch_command(exe or current_executable(), args=args)
    if command is None:
        return False
    env[MARKER] = "1"
    execv(command[0], command)
    return True  # only reached by a fake execv in tests
