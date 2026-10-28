"""Ask for administrator rights exactly once, for the one thing setup needs (via pkexec/polkit)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from fwmanager import paths

PKEXEC = "/usr/bin/pkexec"
HELPER = paths.SETUP_HELPER

# pkexec: 126 = the person dismissed the password dialog, 127 = not authorised / wrong password
DISMISSED, NOT_AUTHORISED = 126, 127


@dataclass(frozen=True)
class HelperResult:
    code: int

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def dismissed(self) -> bool:
        return self.code == DISMISSED

    @property
    def not_authorised(self) -> bool:
        return self.code == NOT_AUTHORISED

    @property
    def available(self) -> bool:
        return self.code != -1


def run_setup_helper(
    pkexec: str = PKEXEC, helper: str = HELPER, run=subprocess.run, verb: str = "setup"
) -> HelperResult:
    """Blocking (the password dialog is modal for the user): call it from a worker thread.

    ``verb`` is ``setup`` or ``uninstall``; each has its own polkit action and message.
    """
    if verb not in ("setup", "uninstall"):
        raise ValueError(verb)
    try:
        done = run([pkexec, helper, verb], capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return HelperResult(-1)  # no pkexec here: the wizard falls back to showing a command
    return HelperResult(done.returncode)
