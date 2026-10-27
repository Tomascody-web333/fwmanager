"""UfwBackend against the real ufw + iptables-nft, in a private namespace."""

import re
import subprocess

import pytest

from fwmanager.core import ufw_files
from fwmanager.core.backend import Options
from fwmanager.core.profiles import DEFAULT_PROFILES, Profile, Source
from fwmanager.core.ufw_runner import UfwError, UfwMissing

pytestmark = pytest.mark.integration


def iptables_save(prefix):
    out = subprocess.run(
        [*prefix, "iptables-save"], capture_output=True, text=True, check=True
    ).stdout
    keep = [ln for ln in out.splitlines() if not ln.startswith("#")]
    return [re.sub(r"\[\d+:\d+\]", "[0:0]", ln) for ln in keep]


def managed(backend):
    return {r: p for r, p in backend.listing().managed.items()}


def files_of(backend):
    root = backend.ufw.rootdir
    return {rel: (root / rel).read_bytes() for rel in ufw_files.SNAPSHOT_FILES}


def test_publico_is_active_closed_and_marked(backend, netns_prefix):
    assert not backend.is_active()
    backend.apply(DEFAULT_PROFILES["publico"])
    assert backend.is_active()
    assert backend.active_profile() == "publico"
    assert managed(backend) == {}
    rules = "\n".join(iptables_save(netns_prefix))
    assert ":INPUT DROP" in rules
    assert ":FORWARD ACCEPT" in rules  # not router mode: Docker/VMs keep forwarding
    assert "--icmp-type 8 -j DROP" in rules  # ping off in publico
    assert "224.0.0.251" in rules and "DROP" in rules
    verbose = backend.ufw.run(["status", "verbose"])
    assert "deny (incoming), allow (outgoing)" in verbose


def test_casa_opens_its_services_and_keeps_ping(backend, netns_prefix):
    backend.apply(DEFAULT_PROFILES["casa"])
    got = managed(backend)
    assert got and set(got.values()) == {"casa"}
    assert {r.source for r in got} >= {"192.168.0.0/16", "10.0.0.0/8"}
    rules = "\n".join(iptables_save(netns_prefix))
    assert "--dports 22,139,445,631,1714:1764,22000" in rules
    assert "--icmp-type 8 -j DROP" not in rules


def test_switching_profiles_replaces_only_our_rules(backend):
    backend.ufw.run(["--force", "enable"])
    backend.ufw.run(["allow", "8080/tcp", "comment", "mine"])  # the user's own rule
    backend.apply(DEFAULT_PROFILES["casa"])
    backend.apply(DEFAULT_PROFILES["trabalho"])
    assert set(managed(backend).values()) == {"trabalho"}
    assert all(r.ports == ("22",) for r in managed(backend))
    backend.apply(DEFAULT_PROFILES["publico"])
    assert managed(backend) == {}
    added = backend.ufw.run(["show", "added"])
    assert "8080" in added and "comment 'mine'" in added  # untouched through every switch


def test_reapplying_the_same_profile_changes_nothing(backend):
    backend.apply(DEFAULT_PROFILES["casa"])
    before = files_of(backend)
    plan = backend.plan(DEFAULT_PROFILES["casa"])
    assert plan.add == [] and plan.remove == []
    backend.apply(DEFAULT_PROFILES["casa"])
    assert files_of(backend) == before


def test_identical_user_rule_is_not_duplicated_nor_deleted(backend):
    backend.ufw.run(["--force", "enable"])
    backend.ufw.run(["allow", "from", "any", "to", "any", "port", "22", "proto", "tcp"])
    ssh_any = Profile("personal", services=("ssh",), source=Source("any"))
    backend.apply(ssh_any)
    assert managed(backend) == {}  # the user's rule already does the job
    backend.apply(DEFAULT_PROFILES["publico"])
    assert "22/tcp" in backend.ufw.run(["show", "added"])  # still there


def test_snapshot_restore_from_inactive_is_exact(backend, netns_prefix):
    files_before = files_of(backend)
    ipt_before = iptables_save(netns_prefix)
    snap = backend.snapshot()
    assert not snap.was_active
    backend.apply(DEFAULT_PROFILES["casa"])
    assert backend.is_active() and files_of(backend) != files_before
    backend.restore(snap)
    assert not backend.is_active()
    assert files_of(backend) == files_before
    # `ufw disable` leaves empty chains behind; what matters is that nothing filters any more.
    after = iptables_save(netns_prefix)
    assert ipt_before == []
    assert ":INPUT ACCEPT [0:0]" in after and ":FORWARD ACCEPT [0:0]" in after
    assert not any(" -j DROP" in ln or "REJECT" in ln for ln in after if ln.startswith("-A INPUT"))


def test_snapshot_restore_from_active_is_exact(backend, netns_prefix):
    backend.apply(DEFAULT_PROFILES["trabalho"])
    files_before = files_of(backend)
    ipt_before = iptables_save(netns_prefix)
    snap = backend.snapshot()
    assert snap.was_active
    backend.apply(DEFAULT_PROFILES["publico"], Options(router_mode=True))
    assert files_of(backend) != files_before
    backend.restore(snap)
    assert backend.is_active() and backend.active_profile() == "trabalho"
    assert files_of(backend) == files_before
    assert iptables_save(netns_prefix) == ipt_before


def test_router_mode_toggles_forwarding(backend):
    backend.apply(DEFAULT_PROFILES["publico"])
    assert 'DEFAULT_FORWARD_POLICY="ACCEPT"' in backend.ufw.default_file.read_text()
    backend.apply(DEFAULT_PROFILES["publico"], Options(router_mode=True))
    assert 'DEFAULT_FORWARD_POLICY="DROP"' in backend.ufw.default_file.read_text()


def test_unrecognised_before_rules_refuses_and_changes_nothing(backend):
    path = backend.ufw.config_dir / "before.rules"
    path.write_text(path.read_text().replace(ufw_files.ANCHOR_V4, "# anchor removed"))
    files_before = files_of(backend)
    with pytest.raises(ufw_files.UfwUnsupported):
        backend.apply(DEFAULT_PROFILES["casa"])
    assert files_of(backend) == files_before and not backend.is_active()


def test_ipv6_rules_are_skipped_without_kernel_ipv6(backend):
    plan = backend.plan(DEFAULT_PROFILES["casa"])
    assert plan.skipped_v6 == (not backend.ufw.ipv6_enabled())
    assert not any(r.is_v6 for r in plan.add) or backend.ufw.ipv6_enabled()


def test_remove_everything_restores_pristine_files(backend):
    pristine = files_of(backend)
    backend.ufw.run(["--force", "enable"])
    backend.ufw.run(["allow", "8080/tcp", "comment", "mine"])
    backend.apply(DEFAULT_PROFILES["casa"])
    backend.remove_everything()
    assert managed(backend) == {}
    assert ufw_files.active_marker(backend.ufw.config_dir) is None
    assert "8080" in backend.ufw.run(["show", "added"])
    now = files_of(backend)
    assert now["etc/ufw/before.rules"] == pristine["etc/ufw/before.rules"]
    assert now["etc/ufw/before6.rules"] == pristine["etc/ufw/before6.rules"]


def test_logging_option(backend):
    backend.apply(DEFAULT_PROFILES["publico"], Options(log_level="high"))
    assert "LOGLEVEL=high" in (backend.ufw.config_dir / "ufw.conf").read_text()
    backend.apply(DEFAULT_PROFILES["publico"])  # None: leave it alone
    assert "LOGLEVEL=high" in (backend.ufw.config_dir / "ufw.conf").read_text()


def test_missing_ufw_binary():
    from fwmanager.core.ufw_runner import Ufw

    u = Ufw("/nonexistent/ufw")
    assert u.installed() is False
    with pytest.raises(UfwMissing):
        u.run(["status"])


def test_ufw_errors_are_reported(backend):
    with pytest.raises(UfwError):
        backend.ufw.run(["allow", "not-a-port-spec"])
