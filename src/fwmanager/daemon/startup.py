"""Boot-time behaviour: re-apply the last confirmed profile, never lock the user out."""

from __future__ import annotations

import logging
from collections.abc import Callable

from fwmanager.core.conflicts import Conflicts
from fwmanager.core.manager import FirewallManager
from fwmanager.core.profiles import Profile
from fwmanager.core.store import Store
from fwmanager.errors import FwError, StoreCorrupt

log = logging.getLogger("fwmanager")

SAFE_PROFILE = "publico"

# Machine-readable outcome, shown by the UI.
OK = "ok"
FIRST_BOOT = "first_boot"
CONFLICT = "firewall_conflict"
STATE_CORRUPT = "state_corrupt"
FAILED = "apply_failed"


def startup_apply(
    manager: FirewallManager,
    store: Store,
    profiles: dict[str, Profile],
    detect: Callable[[], Conflicts],
) -> str:
    """Apply the saved profile without a confirmation timer (nobody is there to confirm).

    - ufw/firewalld active: apply nothing.
    - no saved state: apply ``publico`` (first boot).
    - damaged or unknown state: apply ``publico`` and log the error.
    """
    conflicts = detect()
    if conflicts.blocking:
        log.error("another firewall is active (%s); not applying anything", conflicts.blocking)
        return CONFLICT

    outcome = OK
    name: str | None
    try:
        name = store.load_state()
        if name is None:
            outcome, name = FIRST_BOOT, SAFE_PROFILE
        elif name not in profiles:
            log.error("saved profile is unknown; falling back to %s", SAFE_PROFILE)
            outcome, name = STATE_CORRUPT, SAFE_PROFILE
    except StoreCorrupt as exc:
        log.error("saved state is damaged (%s); falling back to %s", exc.message, SAFE_PROFILE)
        store.quarantine_state()
        outcome, name = STATE_CORRUPT, SAFE_PROFILE

    try:
        manager.apply(profiles[name], require_confirm=False)
    except FwError as exc:
        log.error("could not apply %s at startup: %s", name, exc.message)
        return FAILED
    log.info("startup: applied %s", name)
    return outcome
