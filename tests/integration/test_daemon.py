"""End-to-end tests: real daemon (inside a netns) on a private bus, real nft."""

import json
import time

import pytest
from gi.repository import GLib
from helpers.harness import Daemon, busctl_as, snapshot_of, wait_until

from fwmanager.cli.main import main as cli_main
from fwmanager.errors import FwError

pytestmark = pytest.mark.integration


@pytest.fixture
def daemon(nft, netns_prefix, private_bus, tmp_path):
    d = Daemon(netns_prefix, private_bus, timeout=2, state_dir=tmp_path / "state")
    d.initial = snapshot_of(nft)  # first boot already applied "publico"
    yield d
    d.stop()


def test_apply_confirm_status(daemon, nft):
    c = daemon.client
    assert set(c.list_profiles()) == {"casa", "trabalho", "publico", "personalizado"}
    c.apply_profile("publico")
    st = c.status()
    assert st["pending_profile"] == "publico" and st["seconds_left"] >= 1
    c.confirm()
    st = c.status()
    assert st["active_profile"] == "publico" and st["pending_profile"] == ""


def test_timeout_reverts_via_daemon(daemon, nft):
    daemon.client.apply_profile("casa")
    assert snapshot_of(nft) != daemon.initial
    assert wait_until(lambda: snapshot_of(nft) == daemon.initial, 6)
    assert daemon.client.status()["pending_profile"] == ""


def test_reverted_signal_delivered(daemon):
    got = []
    daemon.client.subscribe(lambda name, args: got.append((name, args)))
    daemon.client.apply_profile("casa")
    ctx = GLib.MainContext.default()
    end = time.monotonic() + 6
    while time.monotonic() < end and not any(n == "Reverted" for n, _ in got):
        ctx.iteration(False)
        time.sleep(0.02)
    assert ("Applied", ("casa", 2)) in got
    assert ("Reverted", ("casa",)) in got


def test_second_apply_rejected(daemon):
    c = daemon.client
    c.apply_profile("casa")
    with pytest.raises(FwError) as exc:
        c.apply_profile("publico")
    assert exc.value.code == "apply_pending"
    c.rollback()


def test_confirm_without_pending(daemon):
    with pytest.raises(FwError) as exc:
        daemon.client.confirm()
    assert exc.value.code == "no_pending"


@pytest.mark.parametrize(
    "evil",
    ['x"; flush ruleset', "casa\nflush ruleset", "../casa", "CASA", "a b", "", "x" * 64, "{ }"],
)
def test_malicious_profile_names_rejected(daemon, nft, evil):
    with pytest.raises(FwError) as exc:
        daemon.client.apply_profile(evil)
    assert exc.value.code == "invalid_profile"
    assert snapshot_of(nft) == daemon.initial


def test_unknown_profile(daemon, nft):
    with pytest.raises(FwError) as exc:
        daemon.client.apply_profile("naoexiste")
    assert exc.value.code == "unknown_profile"


def test_wrong_argument_types_rejected_by_bus(daemon, nft):
    from gi.repository import Gio

    c = daemon.client
    for sig, val in (("(u)", 5), ("(as)", ["casa"]), ("()", None)):
        with pytest.raises(GLib.Error):
            c._conn.call_sync(
                "org.fwmanager.Control",
                "/org/fwmanager/Control",
                "org.fwmanager.Control",
                "ApplyProfile",
                GLib.Variant(sig, (val,) if val is not None else ()),
                None,
                Gio.DBusCallFlags.NONE,
                3000,
                None,
            )
    assert snapshot_of(nft) == daemon.initial


def test_no_method_accepts_rule_text(daemon):
    """The interface exposes only fixed commands."""
    from fwmanager.daemon.interface import interface_info

    methods = {m.name for m in interface_info().methods}
    assert methods == {
        "ApplyProfile", "Confirm", "Rollback", "GetStatus", "ListProfiles",
        "ListNetworks", "SetNetworkProfile", "RemoveNetwork", "GetNetworkProfile",
    }  # fmt: skip


@pytest.mark.parametrize(
    "method,args", [("GetStatus", []), ("ApplyProfile", ["s", "publico"]), ("Rollback", [])]
)
def test_user_outside_group_is_refused(daemon, nft, method, args):
    r = busctl_as(65534, daemon.bus, method, *args)  # "nobody" is not in group root
    assert r.returncode != 0
    assert "not in the fwmanager group" in r.stderr
    assert snapshot_of(nft) == daemon.initial


def test_user_in_group_is_accepted(daemon):
    r = busctl_as(0, daemon.bus, "GetStatus")
    assert r.returncode == 0, r.stderr


def test_sigterm_with_pending_change_restores_previous_state(nft, netns_prefix, private_bus):
    d = Daemon(netns_prefix, private_bus, timeout=60)
    before = snapshot_of(nft)  # first boot already applied "publico"
    d.client.apply_profile("casa")
    assert snapshot_of(nft) != before
    log = d.stop()
    assert snapshot_of(nft) == before
    assert "Traceback" not in log


def test_daemon_log_has_no_rule_content(daemon):
    daemon.client.apply_profile("casa")
    daemon.client.rollback()
    log = daemon.stop()
    assert "uid" in log and "ApplyProfile" in log
    assert "ip saddr" not in log and "tcp dport" not in log


def test_cli_end_to_end(daemon, nft, capsys):
    bus = ["--bus-address", daemon.bus]
    assert cli_main([*bus, "apply", "publico", "--yes"]) == 0
    assert cli_main([*bus, "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["active_profile"] == "publico"
    assert cli_main([*bus, "confirm"]) == 1  # nothing pending any more
    assert cli_main([*bus, "apply", "naoexiste", "--yes"]) == 1
    assert cli_main([*bus, "profiles"]) == 0


def test_cli_without_daemon(private_bus, capsys):
    assert cli_main(["--bus-address", private_bus, "status"]) == 1
    assert "not running" in capsys.readouterr().err
