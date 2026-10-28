"""Persistence, boot behaviour, conflicts and network mapping against the real daemon."""

import json
import subprocess
import sys

import pytest
from helpers.harness import ROOT, Daemon, busctl_as, ufw_cmd, wait_until

from fwmanager.errors import FwError

pytestmark = pytest.mark.integration

UUID = "123e4567-e89b-12d3-a456-426614174000"


@pytest.fixture
def state(tmp_path):
    return tmp_path / "state"


@pytest.fixture
def make(nft, netns_prefix, private_bus, ufw_root, state):
    """Start daemons against the same ufw tree and state dir; stop them all afterwards."""
    started = []

    def _make(**kw):
        d = Daemon(netns_prefix, private_bus, ufw_root, timeout=kw.pop("timeout", 3),
                   state_dir=state, **kw)  # fmt: skip
        started.append(d)
        return d

    yield _make
    for d in started:
        d.stop()


@pytest.fixture
def fake_systemd(private_bus):
    procs = []

    def _start(*units):
        p = subprocess.Popen(
            [sys.executable, str(ROOT / "tests/helpers/fake_systemd.py"), private_bus, *units],
            stdout=subprocess.PIPE,
            text=True,
        )
        assert p.stdout.readline().strip() == "ready"
        procs.append(p)

    yield _start
    for p in procs:
        p.kill()
        p.wait()


def test_first_boot_applies_publico_without_revert(make):
    d = make()
    st = d.client.status()
    assert st["active_profile"] == "public" and st["firewall_active"]
    assert st["pending_profile"] == "" and st["startup"] == "first_boot"
    # No confirmation timer was started: it survives well past the (3 s) timeout.
    assert not wait_until(lambda: d.fp() != d.initial, 5)
    assert d.client.status()["active_profile"] == "public"


def test_confirmed_profile_is_restored_after_restart(make, nft, state):
    d = make()
    d.client.apply_profile("home")
    d.client.confirm()
    d.stop()
    nft.run(["flush", "ruleset"])  # simulate a reboot: kernel rules gone, files remain
    d2 = make()
    st = d2.client.status()
    assert st["active_profile"] == "home" and st["startup"] == "ok"
    assert json.loads((state / "state.json").read_text())["profile"] == "home"


def test_unconfirmed_profile_is_not_persisted(make, state):
    d = make()
    d.client.apply_profile("home")
    d.client.rollback()
    d.stop()
    assert not (state / "state.json").exists()


@pytest.mark.parametrize("content", ["not json", '{"version":1,"profile":"x\\ny"}', ""])
def test_corrupt_state_falls_back_to_publico(make, state, content):
    state.mkdir()
    (state / "state.json").write_text(content)
    d = make()
    assert d.client.status()["active_profile"] == "public"
    assert d.client.status()["startup"] == "state_corrupt"
    assert (state / "state.json.corrupt").read_text() == content
    assert "damaged" in d.stop()


def test_unknown_profile_in_state_falls_back(make, state):
    state.mkdir()
    (state / "state.json").write_text('{"version": 1, "profile": "desaparecido"}')
    d = make()
    assert d.client.status()["active_profile"] == "public"
    assert d.client.status()["startup"] == "state_corrupt"


def test_firewalld_blocks_everything(make, fake_systemd, ufw_root):
    fake_systemd("firewalld.service")
    d = make()
    st = d.client.status()
    assert st["blocking_conflicts"] == ["firewalld"] and st["startup"] == "firewall_conflict"
    assert not st["firewall_active"] and d.fp() == d.initial  # nothing applied at boot
    with pytest.raises(FwError) as exc:
        d.client.apply_profile("home")
    assert exc.value.code == "firewall_conflict"
    assert d.fp() == d.initial


def test_ufw_service_active_is_not_a_conflict(make, fake_systemd):
    fake_systemd("ufw.service")  # ufw is the engine, not a rival
    d = make()
    st = d.client.status()
    assert st["blocking_conflicts"] == [] and st["active_profile"] == "public"


def test_docker_is_only_a_warning(make, fake_systemd):
    fake_systemd("docker.service")
    d = make()
    st = d.client.status()
    assert st["warnings"] == ["docker"] and st["blocking_conflicts"] == []
    assert st["active_profile"] == "public"


def test_ufw_not_installed_is_reported_not_fatal(make):
    d = make(extra=["--ufw-binary", "/nonexistent/ufw"])
    assert d.client.status()["startup"] == "ufw_missing"
    with pytest.raises(FwError) as exc:
        d.client.apply_profile("home")
    assert exc.value.code == "ufw_missing"
    assert d.client.list_profiles()  # the UI can still talk to it and explain


def test_users_own_ufw_rules_survive_boot_and_profiles(make, ufw_root, netns_prefix):
    ufw_cmd(ufw_root, netns_prefix, "allow", "8080/tcp", "comment", "mine")
    d = make()
    for name in ("home", "work", "public"):
        d.client.apply_profile(name)
        d.client.confirm()
        assert "8080/tcp" in ufw_cmd(ufw_root, netns_prefix, "show", "added")


def test_network_mapping(make):
    d = make()
    c = d.client
    assert c.list_networks() == []
    c.set_network_profile("Casa WiFi", UUID, "home")
    c.set_network_profile("Café", "", "public")
    assert sorted(c.list_networks()) == [("Café", "", "public"), ("Casa WiFi", UUID, "home")]
    assert c.get_network_profile("Casa WiFi", UUID) == "home"
    assert c.get_network_profile("", UUID) == "home"
    assert c.get_network_profile("Café", "") == "public"
    assert c.get_network_profile("Outra", "") == ""
    c.set_network_profile("Casa WiFi", UUID, "work")  # replaces
    assert c.get_network_profile("", UUID) == "work"
    c.remove_network("Café", "")
    assert [n[0] for n in c.list_networks()] == ["Casa WiFi"]
    d.stop()
    d2 = make()  # survives restart
    assert d2.client.get_network_profile("Casa WiFi", UUID) == "work"


@pytest.mark.parametrize(
    "ssid,uuid,profile,code",
    [
        ('x"; drop', "", "home", None),  # a quote is a legal SSID character...
        ("ok", "", 'a"b', "invalid_profile"),
        ("ok", "", "naoexiste", "unknown_profile"),
        ("a\nb", "", "home", "invalid_network"),
        ("", "", "home", "invalid_network"),
        ("ok", "not-a-uuid", "home", "invalid_network"),
        ("x" * 40, "", "home", "invalid_network"),
    ],
)
def test_network_input_validation(make, ssid, uuid, profile, code):
    d = make()
    if code is None:
        d.client.set_network_profile(ssid, uuid, profile)  # stored verbatim, never in a command
        assert d.client.get_network_profile(ssid, uuid) == profile
    else:
        with pytest.raises(FwError) as exc:
            d.client.set_network_profile(ssid, uuid, profile)
        assert exc.value.code == code
        assert d.client.list_networks() == []


def test_network_methods_refuse_outsiders(make):
    d = make()
    r = busctl_as(65534, d.bus, "SetNetworkProfile", "sss", "x", "", "home")
    assert r.returncode != 0 and "not in the fwmanager group" in r.stderr
    assert d.client.list_networks() == []


def test_corrupt_networks_file_reports_error(make, state):
    state.mkdir()
    (state / "networks.json").write_text("{broken")
    d = make()
    with pytest.raises(FwError) as exc:
        d.client.list_networks()
    assert exc.value.code == "state_corrupt"
    assert d.client.status()["active_profile"] == "public"  # firewall unaffected
