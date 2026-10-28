"""End-to-end tests: real daemon (inside a netns) on a private bus, real ufw."""

import json
import signal
import time

import pytest
from gi.repository import Gio, GLib
from helpers.harness import Daemon, busctl_as, wait_until

from fwmanager.cli.main import main as cli_main
from fwmanager.errors import FwError

pytestmark = pytest.mark.integration


def test_first_boot_then_apply_confirm_status(daemon):
    c = daemon.client
    assert set(c.list_profiles()) == {"casa", "trabalho", "publico", "personalizado"}
    assert c.status()["active_profile"] == "publico"  # first boot
    assert c.status()["configured"] is False  # nobody has chosen anything yet: setup to do
    c.apply_profile("casa")
    st = c.status()
    assert st["pending_profile"] == "casa" and st["seconds_left"] >= 1
    assert st["configured"] is False  # not until it is confirmed
    c.confirm()
    st = c.status()
    assert st["active_profile"] == "casa" and st["pending_profile"] == ""
    assert st["configured"] is True  # done once, in any client, done for all of them


def test_timeout_reverts_via_daemon(daemon):
    daemon.client.apply_profile("casa")
    assert daemon.fp() != daemon.initial
    assert wait_until(lambda: daemon.fp() == daemon.initial, 15)
    assert daemon.client.status()["pending_profile"] == ""


def test_reverted_signal_delivered(daemon):
    got = []
    daemon.client.subscribe(lambda name, args: got.append((name, args)))
    daemon.client.apply_profile("casa")
    ctx = GLib.MainContext.default()
    end = time.monotonic() + 15
    while time.monotonic() < end and not any(n == "Reverted" for n, _ in got):
        ctx.iteration(False)
        time.sleep(0.02)
    assert ("Applied", ("casa", 3)) in got
    assert ("Reverted", ("casa",)) in got


def test_second_apply_rejected(daemon):
    c = daemon.client
    c.apply_profile("casa")
    with pytest.raises(FwError) as exc:
        c.apply_profile("trabalho")
    assert exc.value.code == "apply_pending"
    c.rollback()


def test_confirm_without_pending(daemon):
    with pytest.raises(FwError) as exc:
        daemon.client.confirm()
    assert exc.value.code == "no_pending"
    assert exc.value.message == "nothing to confirm"  # the daemon's own text, not "True"


@pytest.mark.parametrize(
    "evil",
    ['x"; ufw disable', "casa\nufw reset", "../casa", "CASA", "a b", "", "x" * 64, "{ }"],
)
def test_malicious_profile_names_rejected(daemon, evil):
    with pytest.raises(FwError) as exc:
        daemon.client.apply_profile(evil)
    assert exc.value.code == "invalid_profile"
    assert daemon.fp() == daemon.initial


def test_unknown_profile(daemon):
    with pytest.raises(FwError) as exc:
        daemon.client.apply_profile("naoexiste")
    assert exc.value.code == "unknown_profile"


def test_wrong_argument_types_rejected_by_bus(daemon):
    for sig, val in (("(u)", 5), ("(as)", ["casa"]), ("()", None)):
        with pytest.raises(GLib.Error):
            daemon.client._conn.call_sync(
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
    assert daemon.fp() == daemon.initial


def test_no_method_accepts_rule_text(daemon):
    """The interface exposes only fixed commands."""
    from fwmanager.daemon.interface import interface_info

    methods = {m.name for m in interface_info().methods}
    assert methods == {
        "ApplyProfile",
        "Confirm",
        "Rollback",
        "GetStatus",
        "ListProfiles",
        "CheckProfile",
        "GetProfile",
        "SetProfile",
        "ResetProfile",
        "ListServices",
        "GetOptions",
        "SetOptions",
        "GetEvents",
        "GetRecentBlocked",
        "ListRules",
        "PreviewRule",
        "AddRule",
        "RemoveRule",
        "ListNetworks",
        "SetNetworkProfile",
        "RemoveNetwork",
        "GetNetworkProfile",
    }


@pytest.mark.parametrize(
    "method,args", [("GetStatus", []), ("ApplyProfile", ["s", "casa"]), ("Rollback", [])]
)
def test_user_outside_group_is_refused(daemon, method, args):
    r = busctl_as(65534, daemon.bus, method, *args)  # "nobody" is not in group root
    assert r.returncode != 0
    assert "not in the fwmanager group" in r.stderr
    assert daemon.fp() == daemon.initial


def test_user_in_group_is_accepted(daemon):
    r = busctl_as(0, daemon.bus, "GetStatus")
    assert r.returncode == 0, r.stderr


def test_sigterm_with_pending_change_restores_previous_state(
    nft, netns_prefix, private_bus, ufw_root
):
    d = Daemon(netns_prefix, private_bus, ufw_root, timeout=60)
    try:
        before = d.fp()  # first boot already applied "publico"
        d.client.apply_profile("casa")
        assert d.fp() != before
        d.proc.send_signal(signal.SIGTERM)
        d.proc.wait(30)
        assert d.fp() == before
        assert "Traceback" not in d.proc.stderr.read()
    finally:
        d.stop()


def test_daemon_log_has_no_rule_content(daemon):
    daemon.client.apply_profile("casa")
    daemon.client.rollback()
    log = daemon.stop()
    assert "uid" in log and "ApplyProfile" in log
    assert "192.168" not in log and "1714" not in log and "fwmanager:casa" not in log


def test_cli_end_to_end(daemon, capsys):
    bus = ["--bus-address", daemon.bus]
    assert cli_main([*bus, "apply", "casa", "--yes"]) == 0
    assert cli_main([*bus, "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["active_profile"] == "casa"
    assert cli_main([*bus, "confirm"]) == 1  # nothing pending any more
    assert cli_main([*bus, "apply", "naoexiste", "--yes"]) == 1
    assert cli_main([*bus, "profiles"]) == 0


def test_cli_without_daemon(private_bus, capsys):
    assert cli_main(["--bus-address", private_bus, "status"]) == 1
    err = capsys.readouterr().err
    assert "not running" in err and "systemctl enable --now fwmanagerd" in err  # what to do


def test_cli_speaks_plainly_and_understands_english_names(daemon, capsys):
    bus = ["--bus-address", daemon.bus]
    assert cli_main([*bus, "apply", "Home", "--yes"]) == 0  # "casa" in English
    capsys.readouterr()
    assert cli_main([*bus, "status"]) == 0
    out = capsys.readouterr().out
    assert "Firewall: on" in out and "Profile in use: Home (casa)" in out
    assert "from the local network only" in out
    assert cli_main([*bus, "profiles"]) == 0
    out = capsys.readouterr().out
    assert "* Home (casa)" in out and "Lets nothing in." in out  # Public
    assert cli_main([*bus, "networks", "list"]) == 0
    assert "No networks remembered yet" in capsys.readouterr().out
    assert cli_main([*bus, "apply", "nope", "--yes"]) == 1
    assert "fwmanager-cli profiles" in capsys.readouterr().err


@pytest.mark.parametrize("command", [["apply", "trabalho"], ["test-policy", "trabalho"]])
def test_ctrl_c_undoes_the_change_at_once(
    nft, netns_prefix, private_bus, ufw_root, tmp_path, command
):
    import os
    import subprocess
    import sys

    from helpers.harness import ROOT

    # A long timeout: only the CLI's own rollback can explain an immediate undo.
    d = Daemon(netns_prefix, private_bus, ufw_root, timeout=60, state_dir=tmp_path / "s")
    try:
        p = subprocess.Popen(
            [*netns_prefix, sys.executable, "-m", "fwmanager.cli.main",
             "--bus-address", private_bus, *command],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        )  # fmt: skip
        assert wait_until(lambda: d.client.status()["pending_profile"] == "trabalho", 20)
        p.send_signal(signal.SIGINT)
        _out, err = p.communicate(timeout=30)
        assert p.returncode == 130 and "Traceback" not in err, err
        st = d.client.status()
        assert st["pending_profile"] == "" and st["active_profile"] == "publico"
        assert d.fp() == d.initial
    finally:
        d.stop()


def test_cli_edits_what_a_profile_blocks_and_restores_it(daemon, capsys):
    bus = ["--bus-address", daemon.bus]
    c = daemon.client
    before = c.get_profile("trabalho")
    assert cli_main([*bus, "edit", "work", "--allow", "samba", "web", "--block", "ssh",
                     "--ping", "off", "--from", "192.168.1.0/24"]) == 0  # fmt: skip
    p = c.get_profile("trabalho")
    assert set(p["services"]) == {"samba", "web"} and p["ping"] is False
    assert p["source_kind"] == "cidrs" and p["cidrs"] == ["192.168.1.0/24"] and p["customized"]
    out = capsys.readouterr().out
    assert "Saved. Now Work (trabalho):" in out and "only from the networks you chose" in out
    assert cli_main([*bus, "edit", "work", "--allow", "nope"]) == 1
    err = capsys.readouterr().err
    assert "Unknown service: nope" in err and "ssh" in err  # and what can be used
    assert cli_main([*bus, "edit", "work", "--from", "192.168.1.0/16"]) == 1
    assert "Did you mean 192.168.0.0/16?" in capsys.readouterr().err
    assert cli_main([*bus, "reset", "work"]) == 0
    assert c.get_profile("trabalho") == before
    assert "back to its defaults" in capsys.readouterr().out
