"""Policy test (static + live) against the real daemon and nft inside a netns."""

import pytest
from helpers.harness import (
    Daemon,
    bring_lo_up,
    busctl_as,
    run_cli_in_ns,
    snapshot_of,
)

from fwmanager import policytest as pt
from fwmanager.errors import FwError

pytestmark = pytest.mark.integration


@pytest.fixture
def env(nft, netns_prefix, private_bus, tmp_path):
    bring_lo_up(netns_prefix)
    d = Daemon(netns_prefix, private_bus, timeout=10, state_dir=tmp_path / "state")
    d.initial = snapshot_of(nft)
    yield d
    d.stop()


def test_check_profile_over_dbus_is_read_only(env, nft):
    for name in ("casa", "trabalho", "publico", "personalizado"):
        findings = env.client.check_profile(name)
        assert [f for f in findings if f[0] == "error"] == [], findings
    assert snapshot_of(nft) == env.initial  # nothing was applied
    assert env.client.status()["pending_profile"] == ""


@pytest.mark.parametrize("bad", ['x"; flush ruleset', "../casa", "CASA", ""])
def test_check_profile_validates_input(env, bad):
    with pytest.raises(FwError) as e:
        env.client.check_profile(bad)
    assert e.value.code == "invalid_profile"
    with pytest.raises(FwError) as e:
        env.client.check_profile("naoexiste")
    assert e.value.code == "unknown_profile"


def test_check_profile_refused_for_outsiders(env):
    r = busctl_as(65534, env.bus, "CheckProfile", "s", "casa")
    assert r.returncode != 0 and "not in the fwmanager group" in r.stderr


def test_cli_static_policy_test(env, netns_prefix, nft):
    r = run_cli_in_ns(netns_prefix, env.bus, "test-policy", "casa", "--static")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Result: SAFE" in r.stdout and "Live test" not in r.stdout
    assert snapshot_of(nft) == env.initial


def test_cli_live_policy_test_restores_everything(env, netns_prefix, nft):
    env.client.apply_profile("trabalho")
    env.client.confirm()
    before = snapshot_of(nft)
    r = run_cli_in_ns(netns_prefix, env.bus, "test-policy", "publico")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Local IPv4 connections: ok -> ok" in r.stdout
    assert "Result: SAFE" in r.stdout
    assert snapshot_of(nft) == before  # exactly the previous rules again
    st = env.client.status()
    assert st["active_profile"] == "trabalho" and st["pending_profile"] == ""


def test_cli_policy_test_errors(env, netns_prefix):
    r = run_cli_in_ns(netns_prefix, env.bus, "test-policy", "naoexiste")
    assert r.returncode == 1
    env.client.apply_profile("casa")  # leaves a change pending
    r = run_cli_in_ns(netns_prefix, env.bus, "test-policy", "publico")
    assert r.returncode == 1  # refuses to run on top of a pending change
    env.client.rollback()


def test_real_regression_is_caught_and_undone(env, nft):
    """A probe that sees the connection die after the change => unsafe, and rules restored."""
    before = snapshot_of(nft)

    def probes():
        alive = snapshot_of(nft) == before
        return [pt.Probe("dns", alive), pt.Probe("loopback_v4", True)]

    result = pt.run_live_test(env.client, "casa", probes)
    assert result.regressions == ["dns"] and result.verdict == "unsafe" and result.restored
    assert snapshot_of(nft) == before
    assert not (env.client.status()["pending_profile"])


def test_killed_test_is_still_reverted_by_the_daemon(nft, netns_prefix, private_bus, tmp_path):
    """If the test process dies mid-way the daemon's own timer restores the rules."""
    import time

    bring_lo_up(netns_prefix)
    d = Daemon(netns_prefix, private_bus, timeout=2, state_dir=tmp_path / "state")
    try:
        before = snapshot_of(nft)

        def crash():
            if snapshot_of(nft) != before:
                raise KeyboardInterrupt  # simulates being killed after the apply
            return [pt.Probe("dns", True)]

        with pytest.raises(KeyboardInterrupt):
            # skip the `finally` rollback by killing at the apply: emulate with a bare apply
            d.client.apply_profile("casa")
            crash()
        end = time.monotonic() + 6
        while time.monotonic() < end and snapshot_of(nft) != before:
            time.sleep(0.1)
        assert snapshot_of(nft) == before
    finally:
        d.stop()
