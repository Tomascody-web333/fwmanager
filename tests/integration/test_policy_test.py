"""Policy test (static + live) against the real daemon and ufw inside a netns."""

import pytest
from helpers.harness import (
    Daemon,
    bring_lo_up,
    busctl_as,
    run_cli_in_ns,
    wait_until,
)

from fwmanager import policytest as pt
from fwmanager.errors import FwError

pytestmark = pytest.mark.integration


@pytest.fixture
def env(nft, netns_prefix, private_bus, tmp_path, ufw_root):
    bring_lo_up(netns_prefix)
    d = Daemon(netns_prefix, private_bus, ufw_root, timeout=20, state_dir=tmp_path / "state")
    yield d
    d.stop()


def test_check_profile_over_dbus_is_read_only(env):
    for name in ("casa", "trabalho", "publico", "personalizado"):
        findings = env.client.check_profile(name)
        assert [f for f in findings if f[0] == "error"] == [], findings
    assert env.fp() == env.initial  # nothing was applied
    assert env.client.status()["pending_profile"] == ""


def test_check_profile_previews_what_will_change(env):
    codes = {f[1]: f[2] for f in env.client.check_profile("casa")}
    assert "will_add" in codes and "192.168.0.0/16" in codes["will_add"]
    env.client.apply_profile("casa")
    env.client.confirm()
    after = {f[1] for f in env.client.check_profile("casa")}
    assert "will_add" not in after and "will_remove" not in after  # already in place
    removal = {f[1] for f in env.client.check_profile("publico")}
    assert "will_remove" in removal


@pytest.mark.parametrize("bad", ['x"; ufw disable', "../casa", "CASA", ""])
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


def test_check_flags_a_damaged_ufw_config(env):
    path = env.ufw_root / "etc/ufw/before.rules"
    path.write_text(path.read_text().replace("-i lo -j ACCEPT", "-i lo -j RETURN"))
    findings = env.client.check_profile("casa")
    assert any(f[0] == "error" and f[1] == "missing_loopback" for f in findings)


def assert_safe(r):
    """Safe, or safe-with-warnings where the only warning is IPv6 missing from this kernel."""
    assert r.returncode in (0, 2), r.stdout + r.stderr
    assert "UNSAFE" not in r.stdout
    warnings = [ln for ln in r.stdout.splitlines() if ln.strip().startswith("[warning]")]
    assert all("IPv6" in ln for ln in warnings), warnings


def test_cli_static_policy_test(env, netns_prefix):
    r = run_cli_in_ns(netns_prefix, env.bus, "test-policy", "casa", "--static")
    assert_safe(r)
    assert "Result: SAFE" in r.stdout and "Live test" not in r.stdout
    assert env.fp() == env.initial


def test_cli_live_policy_test_restores_everything(env, netns_prefix):
    env.client.apply_profile("trabalho")
    env.client.confirm()
    before = env.fp()
    r = run_cli_in_ns(netns_prefix, env.bus, "test-policy", "casa")
    assert_safe(r)
    assert "Local IPv4 connections: ok -> ok" in r.stdout
    assert "Result: SAFE" in r.stdout
    assert env.fp() == before  # exactly the previous rules and files again
    st = env.client.status()
    assert st["active_profile"] == "trabalho" and st["pending_profile"] == ""


def test_cli_policy_test_errors(env, netns_prefix):
    r = run_cli_in_ns(netns_prefix, env.bus, "test-policy", "naoexiste")
    assert r.returncode == 1
    env.client.apply_profile("casa")  # leaves a change pending
    r = run_cli_in_ns(netns_prefix, env.bus, "test-policy", "publico")
    assert r.returncode == 1  # refuses to run on top of a pending change
    env.client.rollback()


def test_real_regression_is_caught_and_undone(env):
    """A probe that sees the connection die after the change => unsafe, and rules restored."""
    before = env.fp()

    def probes():
        alive = env.fp() == before
        return [pt.Probe("dns", alive), pt.Probe("loopback_v4", True)]

    result = pt.run_live_test(env.client, "casa", probes)
    assert result.regressions == ["dns"] and result.verdict == "unsafe" and result.restored
    assert env.fp() == before
    assert not env.client.status()["pending_profile"]


def test_killed_test_is_still_reverted_by_the_daemon(nft, netns_prefix, private_bus, ufw_root):
    """If the test process dies mid-way the daemon's own timer restores the rules."""
    d = Daemon(netns_prefix, private_bus, ufw_root, timeout=3)
    try:
        before = d.fp()
        d.client.apply_profile("casa")  # ...and then "the tester is killed": no rollback call
        assert d.fp() != before
        assert wait_until(lambda: d.fp() == before, 20)
    finally:
        d.stop()
