"""Caller authorization: only members of the ``fwmanager`` group may use the daemon."""

from __future__ import annotations

import grp
import pwd

GROUP = "fwmanager"


def is_member(uid: int, group: str = GROUP) -> bool:
    """True if ``uid`` is in ``group``, either as a listed member or by primary group."""
    try:
        g = grp.getgrnam(group)
        user = pwd.getpwuid(uid)
    except KeyError:
        return False
    return user.pw_name in g.gr_mem or user.pw_gid == g.gr_gid
