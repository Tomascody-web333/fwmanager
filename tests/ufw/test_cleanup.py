"""Uninstall-time cleanup and the "did fwmanager enable ufw?" bookkeeping."""

import pytest
from gi.repository import GLib
from helpers.harness import fingerprint, ufw_cmd

from fwmanager.core import ufw_files
from fwmanager.core.manager import FirewallManager
from fwmanager.core.profiles import DEFAULT_PROFILES
from fwmanager.core.scheduler import GLibScheduler
from fwmanager.core.store import Store
from fwmanager.daemon.cleanup import cleanup
from fwmanager.daemon.main import main as daemon_main

pytestmark = pytest.mark.integration


def pump(seconds):
    ctx = GLib.MainContext.default()
    end = GLib.get_monotonic_time() + int(seconds * 1_000_000)
    while GLib.get_monotonic_time() < end:
        ctx.iteration(False)
        GLib.usleep(10_000)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "state")


@pytest.fixture
def mgr(backend, store):
    return FirewallManager(backend, GLibScheduler(), on_ufw_enabled=store.mark_ufw_enabled_by_us)


def test_boot_time_enable_is_recorded(mgr, store):
    assert not store.ufw_enabled_by_us()
    mgr.apply(DEFAULT_PROFILES["publico"], require_confirm=False)
    assert store.ufw_enabled_by_us()


def test_confirmed_enable_is_recorded_but_a_reverted_one_is_not(mgr, store):
    mgr.apply(DEFAULT_PROFILES["casa"], timeout=2)
    assert not store.ufw_enabled_by_us()  # still pending
    pump(6)  # times out and reverts: ufw is off again, nothing to remember
    assert not store.ufw_enabled_by_us()
    mgr.apply(DEFAULT_PROFILES["casa"], timeout=30)
    mgr.confirm()
    assert store.ufw_enabled_by_us()


def test_users_own_enabled_ufw_is_not_claimed(backend, mgr, store, netns_prefix):
    ufw_cmd(backend.ufw.rootdir, netns_prefix, "--force", "enable")
    mgr.apply(DEFAULT_PROFILES["casa"], require_confirm=False)
    assert not store.ufw_enabled_by_us()


def test_cleanup_turns_ufw_off_when_we_turned_it_on(backend, mgr, store, netns_prefix):
    pristine = fingerprint(backend.ufw.rootdir, netns_prefix)[0]
    mgr.apply(DEFAULT_PROFILES["casa"], require_confirm=False)
    assert backend.is_active()
    assert cleanup(backend, store) == "disabled"
    assert not backend.is_active() and not store.ufw_enabled_by_us()
    now = fingerprint(backend.ufw.rootdir, netns_prefix)[0]
    assert now["etc/ufw/before.rules"] == pristine["etc/ufw/before.rules"]
    # ufw itself re-writes user.rules on the first change (empty log chains); no rule is left.
    user_rules = now["etc/ufw/user.rules"].decode()
    assert "fwmanager" not in user_rules and "-A ufw-user-input" not in user_rules
    assert backend.listing().managed == {}


def test_cleanup_leaves_a_users_ufw_running_and_their_rules(backend, mgr, store, netns_prefix):
    root = backend.ufw.rootdir
    ufw_cmd(root, netns_prefix, "--force", "enable")
    ufw_cmd(root, netns_prefix, "allow", "8080/tcp", "comment", "mine")
    mgr.apply(DEFAULT_PROFILES["casa"], require_confirm=False)
    assert cleanup(backend, store) == "cleaned"
    assert backend.is_active()  # theirs to keep
    assert backend.listing().managed == {}
    assert ufw_files.active_marker(backend.ufw.config_dir) is None
    assert "8080/tcp" in ufw_cmd(root, netns_prefix, "show", "added")


def test_cleanup_on_a_machine_fwmanager_never_touched(backend, store, netns_prefix):
    before = fingerprint(backend.ufw.rootdir, netns_prefix)
    assert cleanup(backend, store) == "cleaned"
    assert fingerprint(backend.ufw.rootdir, netns_prefix) == before


def test_cleanup_command_line(backend, mgr, store, tmp_path, capsys):
    mgr.apply(DEFAULT_PROFILES["casa"], require_confirm=False)
    wrapper = str(backend.ufw._binary)
    code = daemon_main(
        [
            "--cleanup",
            "--ufw-binary",
            wrapper,
            "--ufw-rootdir",
            str(backend.ufw.rootdir),
            "--state-dir",
            str(tmp_path / "state"),
        ]
    )
    assert code == 0 and capsys.readouterr().out.strip() == "disabled"
    assert not backend.is_active()


# -- the user's own ufw default policies ---------------------------------------------------


@pytest.fixture
def tracked(backend, store):
    """A backend wired like the daemon: it remembers the user's defaults before changing them."""
    from fwmanager.core.backend import UfwBackend

    return UfwBackend(
        backend.ufw, backend.catalog, on_first_defaults_change=store.save_original_defaults_once
    )


def read_default(backend, key):
    import re

    text = backend.ufw.default_file.read_text()
    return re.search(rf'^{key}="(\w+)"', text, re.M).group(1)


def test_the_original_defaults_come_back_on_uninstall(tracked, store):
    # ufw's own defaults: incoming DROP, forward DROP. fwmanager sets forward to ACCEPT.
    assert read_default(tracked, "DEFAULT_FORWARD_POLICY") == "DROP"
    tracked.apply(DEFAULT_PROFILES["casa"])
    assert read_default(tracked, "DEFAULT_FORWARD_POLICY") == "ACCEPT"
    assert store.load_original_defaults() == {
        "incoming": "deny",
        "outgoing": "allow",
        "routed": "deny",
    }
    cleanup(tracked, store)
    assert read_default(tracked, "DEFAULT_FORWARD_POLICY") == "DROP"  # back to how it was
    assert read_default(tracked, "DEFAULT_INPUT_POLICY") == "DROP"
    assert store.load_original_defaults() == {}  # the record is consumed


def test_defaults_are_remembered_only_once(tracked, store):
    from fwmanager.core.backend import Options

    tracked.apply(DEFAULT_PROFILES["casa"])
    first = store.load_original_defaults()
    tracked.apply(DEFAULT_PROFILES["publico"], Options(router_mode=True))  # routed changes again
    tracked.apply(DEFAULT_PROFILES["trabalho"])
    assert store.load_original_defaults() == first  # still the user's, not ours


def test_a_policy_the_user_changed_since_is_left_alone(tracked, store):
    tracked.apply(DEFAULT_PROFILES["casa"])
    tracked.ufw.run(["default", "reject", "incoming"])  # the user's own later choice
    cleanup(tracked, store)
    assert read_default(tracked, "DEFAULT_INPUT_POLICY") == "REJECT"
    assert read_default(tracked, "DEFAULT_FORWARD_POLICY") == "DROP"  # ours: put back


def test_nothing_is_recorded_when_nothing_changes(tracked, store):
    from fwmanager.core.backend import Options

    tracked.ufw.run(["default", "allow", "routed"])  # user already has what we would set
    tracked.apply(DEFAULT_PROFILES["casa"], Options())
    assert store.load_original_defaults() == {}
    assert not (store._dir / "ufw-original-defaults.json").exists()


def test_corrupt_defaults_record_is_ignored(store):
    store._dir.mkdir()
    (store._dir / "ufw-original-defaults.json").write_text("{broken")
    assert store.load_original_defaults() == {}
    (store._dir / "ufw-original-defaults.json").write_text(
        '{"version": 1, "defaults": {"incoming": "evil", "routed": "allow", "x": "deny"}}'
    )
    assert store.load_original_defaults() == {"routed": "allow"}  # only valid entries survive
