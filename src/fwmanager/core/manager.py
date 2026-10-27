"""Apply / confirm / rollback logic with the anti-lockout timer."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from fwmanager.core import snapshot as snap_mod
from fwmanager.core.nft import Nft
from fwmanager.core.profiles import Profile
from fwmanager.core.rules import FAMILY, TABLE, RenderOptions, render
from fwmanager.core.scheduler import Scheduler
from fwmanager.core.services import Service
from fwmanager.errors import FwError, PendingError

log = logging.getLogger("fwmanager")

CONFIRM_TIMEOUT = 30


@dataclass
class _Pending:
    snapshot: snap_mod.Snapshot
    profile: str
    deadline: float
    handle: object


class FirewallManager:
    """Owns the single pending-apply slot. Never more than one timer exists."""

    def __init__(
        self,
        nft: Nft,
        catalog: dict[str, Service],
        scheduler: Scheduler,
        on_revert: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._nft = nft
        self._catalog = catalog
        self._sched = scheduler
        self.on_revert = on_revert
        self._clock = clock
        self._pending: _Pending | None = None

    def apply(
        self,
        profile: Profile,
        options: RenderOptions | None = None,
        *,
        require_confirm: bool = True,
        timeout: int = CONFIRM_TIMEOUT,
    ) -> None:
        if self._pending is not None:
            raise PendingError("another apply is waiting for confirmation")
        script = render(profile, self._catalog, options)  # validates before touching nft
        before = snap_mod.take(self._nft)
        self._nft.apply_script(script)  # atomic: on failure the old table is intact
        log.info("applied profile %s", profile.name)
        if require_confirm:
            handle = self._sched.call_later(timeout, self._expire)
            self._pending = _Pending(before, profile.name, self._clock() + timeout, handle)

    def confirm(self) -> None:
        if self._pending is None:
            raise PendingError("nothing to confirm", code="no_pending")
        self._sched.cancel(self._pending.handle)
        log.info("profile %s confirmed", self._pending.profile)
        self._pending = None

    def rollback(self) -> None:
        if self._pending is None:
            raise PendingError("nothing to roll back", code="no_pending")
        self._sched.cancel(self._pending.handle)
        self._revert()

    def pending_profile(self) -> str | None:
        return self._pending.profile if self._pending else None

    def seconds_left(self) -> int:
        if self._pending is None:
            return 0
        return max(0, int(self._pending.deadline - self._clock() + 0.999))

    def _expire(self) -> None:
        log.warning("confirmation timed out, restoring previous ruleset")
        try:
            self._revert()
        except FwError:
            pass  # already logged; must not propagate into the main loop

    def _revert(self) -> None:
        pending, self._pending = self._pending, None
        assert pending is not None
        try:
            snap_mod.restore(self._nft, pending.snapshot)
        except FwError:
            log.exception("rollback failed")
            raise
        finally:
            if self.on_revert:
                self.on_revert(pending.profile)
        log.info("rolled back profile %s", pending.profile)

    def status(self) -> dict:
        """Active profile (from the table comment) and pending state."""
        objs = self._nft.list_table_json(FAMILY, TABLE)
        active = None
        if objs is not None:
            for obj in objs:
                comment = obj.get("table", {}).get("comment", "")
                if comment.startswith("profile:"):
                    active = comment[len("profile:") :]
        return {
            "table_present": objs is not None,
            "active_profile": active,
            "pending_profile": self.pending_profile(),
            "seconds_left": self.seconds_left(),
        }
