"""Apply / confirm / rollback logic with the anti-lockout timer."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from fwmanager.core.backend import Options, Snapshot, UfwBackend
from fwmanager.core.profiles import Profile
from fwmanager.core.scheduler import Scheduler
from fwmanager.errors import FwError, PendingError

log = logging.getLogger("fwmanager")

CONFIRM_TIMEOUT = 30


@dataclass
class _Pending:
    snapshot: Snapshot
    profile: str
    deadline: float
    handle: object


class FirewallManager:
    """Owns the single pending-apply slot. Never more than one timer exists."""

    def __init__(
        self,
        backend: UfwBackend,
        scheduler: Scheduler,
        on_revert: Callable[[str], None] | None = None,
        on_ufw_enabled: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.backend = backend
        self._sched = scheduler
        self.on_revert = on_revert
        self.on_ufw_enabled = on_ufw_enabled
        self._clock = clock
        self._pending: _Pending | None = None

    @property
    def catalog(self):
        return self.backend.catalog

    def apply(
        self,
        profile: Profile,
        options: Options | None = None,
        *,
        require_confirm: bool = True,
        timeout: int = CONFIRM_TIMEOUT,
    ) -> None:
        if self._pending is not None:
            raise PendingError("another apply is waiting for confirmation")
        self.backend.plan(profile)  # validates the profile and ufw's state before any change
        before = self.backend.snapshot()
        try:
            self.backend.apply(profile, options)
        except FwError:
            # ufw cannot apply a profile atomically: undo whatever got through.
            log.error("apply of %s failed midway; restoring the previous state", profile.name)
            self.backend.restore(before)
            raise
        log.info("applied profile %s", profile.name)
        if not require_confirm and not before.was_active:
            self._ufw_enabled_by_us()  # nobody to confirm (boot): the change is final
        if require_confirm:
            handle = self._sched.call_later(timeout, self._expire)
            self._pending = _Pending(before, profile.name, self._clock() + timeout, handle)

    def confirm(self) -> None:
        if self._pending is None:
            raise PendingError("nothing to confirm", code="no_pending")
        self._sched.cancel(self._pending.handle)
        log.info("profile %s confirmed", self._pending.profile)
        if not self._pending.snapshot.was_active:
            self._ufw_enabled_by_us()
        self._pending = None

    def _ufw_enabled_by_us(self) -> None:
        if self.on_ufw_enabled:
            try:
                self.on_ufw_enabled()
            except FwError:
                log.exception("could not record that fwmanager enabled ufw")

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
            self.backend.restore(pending.snapshot)
        except FwError:
            log.exception("rollback failed")
            raise
        finally:
            if self.on_revert:
                self.on_revert(pending.profile)
        log.info("rolled back profile %s", pending.profile)

    def status(self) -> dict:
        """Whether ufw is filtering, which profile is active, and the pending state."""
        try:
            installed = self.backend.ufw.installed()
            active = installed and self.backend.is_active()
        except FwError:
            installed, active = self.backend.ufw.installed(), False
        return {
            "ufw_installed": installed,
            "firewall_active": active,
            "active_profile": self.backend.active_profile() if active else None,
            "pending_profile": self.pending_profile(),
            "seconds_left": self.seconds_left(),
        }
