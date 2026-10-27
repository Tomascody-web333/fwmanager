"""Persistence, boot behaviour, conflicts and network mapping against the real daemon."""

import json
import subprocess
import sys

import pytest
from helpers.harness import ROOT, Daemon, busctl_as, table_exists, wait_until

from fwmanager.errors import FwError

pytestmark = pytest.mark.integration

UUID = "123e4567-e89b-12d3-a456-426614174000"


@pytest.fixture
def state(tmp_path):
    return tmp_path / "state"


def start(netns_prefix, bus, state, **kw):
    return Daemon(netns_prefix, bus, timeout=kw.pop("timeout", 2), state_dir=state, **kw)


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


def active_profile(nft):
    objs = nft.list_table_json("inet", "fwmanager") or []
    for o in objs:
        c = o.get("table", {}).get("comment", "")
        if c.startswith("profile:"):
            return c[8:]


def test_first_boot_applies_publico_without_revert(nft, netns_prefix, private_bus, state):
    d = start(netns_prefix, private_bus, state)
    try:
        assert active_profile(nft) == "publico"
        st = d.client.status()
        assert st["pending_profile"] == "" and st["startup"] == "first_boot"
        # No confirmation timer was started: the table survives past the (2 s) timeout.
        assert not wait_until(lambda: not table_exists(nft), 3.5)
    finally:
        d.stop()


def test_confirmed_profile_is_restored_after_restart(nft, netns_prefix, private_bus, state):
    d = start(netns_prefix, private_bus, state)
    d.client.apply_profile("casa")
    d.client.confirm()
    d.stop()
    nft.run(["flush", "ruleset"])  # simulate a reboot
    d2 = start(netns_prefix, private_bus, state)
    try:
        assert active_profile(nft) == "casa"
        assert d2.client.status()["startup"] == "ok"
        assert json.loads((state / "state.json").read_text())["profile"] == "casa"
    finally:
        d2.stop()


def test_unconfirmed_profile_is_not_persisted(nft, netns_prefix, private_bus, state):
    d = start(netns_prefix, private_bus, state)
    d.client.apply_profile("casa")
    d.client.rollback()
    d.stop()
    assert not (state / "state.json").exists()


@pytest.mark.parametrize("content", ["not json", '{"version":1,"profile":"x\\ny"}', ""])
def test_corrupt_state_falls_back_to_publico(nft, netns_prefix, private_bus, state, content):
    state.mkdir()
    (state / "state.json").write_text(content)
    d = start(netns_prefix, private_bus, state)
    try:
        assert active_profile(nft) == "publico"
        assert d.client.status()["startup"] == "state_corrupt"
        assert (state / "state.json.corrupt").read_text() == content
    finally:
        log = d.stop()
    assert "damaged" in log


def test_unknown_profile_in_state_falls_back(nft, netns_prefix, private_bus, state):
    state.mkdir()
    (state / "state.json").write_text('{"version": 1, "profile": "desaparecido"}')
    d = start(netns_prefix, private_bus, state)
    try:
        assert active_profile(nft) == "publico"
        assert d.client.status()["startup"] == "state_corrupt"
    finally:
        d.stop()


@pytest.mark.parametrize("unit,name", [("ufw.service", "ufw"), ("firewalld.service", "firewalld")])
def test_active_firewall_blocks_everything(
    nft, netns_prefix, private_bus, state, fake_systemd, unit, name
):
    fake_systemd(unit)
    d = start(netns_prefix, private_bus, state)
    try:
        assert not table_exists(nft)  # nothing applied at boot
        st = d.client.status()
        assert st["blocking_conflicts"] == [name] and st["startup"] == "firewall_conflict"
        with pytest.raises(FwError) as exc:
            d.client.apply_profile("casa")
        assert exc.value.code == "firewall_conflict"
        assert not table_exists(nft)
    finally:
        d.stop()


def test_docker_is_only_a_warning(nft, netns_prefix, private_bus, state, fake_systemd):
    fake_systemd("docker.service")
    d = start(netns_prefix, private_bus, state)
    try:
        st = d.client.status()
        assert st["warnings"] == ["docker"] and st["blocking_conflicts"] == []
        assert active_profile(nft) == "publico"
    finally:
        d.stop()


def test_foreign_tables_are_preserved_at_boot(nft, netns_prefix, private_bus, state):
    nft.run(["add", "table", "ip", "docker-bridges"])
    d = start(netns_prefix, private_bus, state)
    try:
        assert "ip docker-bridges" in nft.run(["list", "tables"])
        assert d.client.status()["warnings"] == ["docker"]
    finally:
        d.stop()


def test_network_mapping(nft, netns_prefix, private_bus, state):
    d = start(netns_prefix, private_bus, state)
    c = d.client
    try:
        assert c.list_networks() == []
        c.set_network_profile("Casa WiFi", UUID, "casa")
        c.set_network_profile("Café", "", "publico")
        assert sorted(c.list_networks()) == [("Café", "", "publico"), ("Casa WiFi", UUID, "casa")]
        assert c.get_network_profile("Casa WiFi", UUID) == "casa"
        assert c.get_network_profile("", UUID) == "casa"
        assert c.get_network_profile("Café", "") == "publico"
        assert c.get_network_profile("Outra", "") == ""
        c.set_network_profile("Casa WiFi", UUID, "trabalho")  # replaces
        assert c.get_network_profile("", UUID) == "trabalho"
        c.remove_network("Café", "")
        assert [n[0] for n in c.list_networks()] == ["Casa WiFi"]
    finally:
        d.stop()
    d2 = start(netns_prefix, private_bus, state)  # survives restart
    try:
        assert d2.client.get_network_profile("Casa WiFi", UUID) == "trabalho"
    finally:
        d2.stop()


@pytest.mark.parametrize(
    "ssid,uuid,profile,code",
    [
        ('x"; drop', "", "casa", None),  # a quote is a legal SSID character...
        ("ok", "", 'a"b', "invalid_profile"),
        ("ok", "", "naoexiste", "unknown_profile"),
        ("a\nb", "", "casa", "invalid_network"),
        ("", "", "casa", "invalid_network"),
        ("ok", "not-a-uuid", "casa", "invalid_network"),
        ("x" * 40, "", "casa", "invalid_network"),
    ],
)
def test_network_input_validation(nft, netns_prefix, private_bus, state, ssid, uuid, profile, code):
    d = start(netns_prefix, private_bus, state)
    try:
        if code is None:
            d.client.set_network_profile(ssid, uuid, profile)  # stored verbatim, never in rules
            assert d.client.get_network_profile(ssid, uuid) == profile
        else:
            with pytest.raises(FwError) as exc:
                d.client.set_network_profile(ssid, uuid, profile)
            assert exc.value.code == code
            assert d.client.list_networks() == []
    finally:
        d.stop()


def test_network_methods_refuse_outsiders(nft, netns_prefix, private_bus, state):
    d = start(netns_prefix, private_bus, state)
    try:
        r = busctl_as(65534, d.bus, "SetNetworkProfile", "sss", "x", "", "casa")
        assert r.returncode != 0 and "not in the fwmanager group" in r.stderr
        assert d.client.list_networks() == []
    finally:
        d.stop()


def test_corrupt_networks_file_reports_error(nft, netns_prefix, private_bus, state):
    state.mkdir()
    (state / "networks.json").write_text("{broken")
    d = start(netns_prefix, private_bus, state)
    try:
        with pytest.raises(FwError) as exc:
            d.client.list_networks()
        assert exc.value.code == "state_corrupt"
        assert active_profile(nft) == "publico"  # firewall unaffected
    finally:
        d.stop()
