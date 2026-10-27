"""FirewallManager (confirm/rollback/timer) over the real ufw backend, real GLib timers."""

import pytest
from gi.repository import GLib
from helpers.harness import fingerprint, ufw_cmd

from fwmanager.core.manager import FirewallManager
from fwmanager.core.profiles import DEFAULT_PROFILES
from fwmanager.core.scheduler import GLibScheduler
from fwmanager.core.ufw_runner import UfwError
from fwmanager.errors import PendingError

pytestmark = pytest.mark.integration


class CountingScheduler(GLibScheduler):
    def __init__(self):
        super().__init__()
        self.scheduled = 0

    def call_later(self, seconds, callback):
        self.scheduled += 1
        return super().call_later(seconds, callback)


@pytest.fixture
def mgr(backend):
    return FirewallManager(backend, CountingScheduler())


@pytest.fixture
def fp(backend, netns_prefix):
    return lambda: fingerprint(backend.ufw.rootdir, netns_prefix)


def pump(seconds):
    """Run the default GLib main context (the timers live there)."""
    ctx = GLib.MainContext.default()
    end = GLib.get_monotonic_time() + int(seconds * 1_000_000)
    while GLib.get_monotonic_time() < end:
        ctx.iteration(False)
        GLib.usleep(10_000)


def test_apply_and_status(mgr):
    mgr.apply(DEFAULT_PROFILES["casa"], require_confirm=False)
    st = mgr.status()
    assert st["firewall_active"] and st["active_profile"] == "casa"
    assert st["pending_profile"] is None


def test_confirm_cancels_the_timer(mgr):
    mgr.apply(DEFAULT_PROFILES["publico"], timeout=3)
    assert mgr.pending_profile() == "publico" and 1 <= mgr.seconds_left() <= 3
    mgr.confirm()
    pump(4.5)
    assert mgr.status()["active_profile"] == "publico" and mgr.pending_profile() is None


def test_timeout_restores_the_exact_previous_state(mgr, fp):
    mgr.apply(DEFAULT_PROFILES["casa"], require_confirm=False)
    before = fp()
    mgr.apply(DEFAULT_PROFILES["publico"], timeout=2)
    assert fp() != before
    pump(6)
    assert mgr.pending_profile() is None
    assert fp() == before
    assert mgr.status()["active_profile"] == "casa"


def test_timeout_from_a_clean_machine_turns_ufw_back_off(mgr, backend, fp):
    before = fp()
    assert not backend.is_active()
    reverted = []
    mgr.on_revert = reverted.append
    mgr.apply(DEFAULT_PROFILES["publico"], timeout=2)
    assert backend.is_active()
    pump(6)
    assert not backend.is_active() and fp()[0] == before[0] and reverted == ["publico"]


def test_manual_rollback(mgr, fp):
    mgr.apply(DEFAULT_PROFILES["trabalho"], require_confirm=False)
    before = fp()
    mgr.apply(DEFAULT_PROFILES["casa"])
    mgr.rollback()
    assert fp() == before


def test_second_apply_rejected_with_a_single_timer(mgr, fp):
    sched = mgr._sched
    mgr.apply(DEFAULT_PROFILES["casa"], timeout=30)
    after_first = fp()
    with pytest.raises(PendingError) as exc:
        mgr.apply(DEFAULT_PROFILES["publico"], timeout=30)
    assert exc.value.code == "apply_pending" and sched.scheduled == 1
    assert fp() == after_first
    mgr.rollback()


def test_confirm_or_rollback_without_pending(mgr):
    for fn in (mgr.confirm, mgr.rollback):
        with pytest.raises(PendingError) as exc:
            fn()
        assert exc.value.code == "no_pending"


def test_apply_that_fails_midway_is_undone(mgr, backend, fp, monkeypatch):
    """ufw is not atomic: if the 2nd rule fails, the 1st must not stay behind."""
    mgr.apply(DEFAULT_PROFILES["trabalho"], require_confirm=False)
    before = fp()
    real_run = backend.ufw.run
    seen = {"allow": 0}

    def flaky(args):
        if list(args)[:1] == ["allow"]:
            seen["allow"] += 1
            if seen["allow"] == 2:
                raise UfwError("simulated failure")
        return real_run(args)

    monkeypatch.setattr(backend.ufw, "run", flaky)
    with pytest.raises(UfwError):
        mgr.apply(DEFAULT_PROFILES["casa"])
    monkeypatch.undo()
    assert seen["allow"] == 2  # it really did get past the first rule
    assert mgr.pending_profile() is None
    assert fp() == before


def test_users_own_rules_survive_every_path(mgr, backend, netns_prefix):
    ufw_cmd(backend.ufw.rootdir, netns_prefix, "--force", "enable")
    ufw_cmd(backend.ufw.rootdir, netns_prefix, "allow", "8080/tcp", "comment", "mine")
    mgr.apply(DEFAULT_PROFILES["casa"], timeout=2)
    mgr.rollback()
    mgr.apply(DEFAULT_PROFILES["publico"], require_confirm=False)
    assert "8080/tcp" in backend.ufw.run(["show", "added"])
