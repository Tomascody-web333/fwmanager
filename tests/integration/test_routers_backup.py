"""Routers behind remembered networks, and export / import, against the real daemon."""

import json

import pytest
from helpers.harness import Daemon

from fwmanager.errors import FwError

pytestmark = pytest.mark.integration

UUID = "123e4567-e89b-12d3-a456-426614174000"
R1, R2 = "aa:bb:cc:00:11:22", "aa:bb:cc:00:11:99"


@pytest.fixture
def make(nft, netns_prefix, private_bus, ufw_root, tmp_path):
    started = []

    def _make(state=None):
        d = Daemon(
            netns_prefix, private_bus, ufw_root, timeout=3, state_dir=state or tmp_path / "s"
        )
        started.append(d)
        return d

    yield _make
    for d in started:
        d.stop()


def test_the_first_router_is_remembered_and_then_required(make):
    c = make().client
    c.set_network_profile("Casa WiFi", UUID, "home")
    assert c.check_network("Casa WiFi", UUID, R1) == ("home", "learned")
    assert c.list_network_routers() == [("Casa WiFi", UUID, [R1], "")]
    assert c.check_network("Casa WiFi", UUID, R1) == ("home", "known")
    # A copy of the Wi-Fi (same name, same password) behind another router: not trusted.
    assert c.check_network("Casa WiFi", UUID, R2) == ("", "changed")
    assert c.check_network("Casa WiFi", UUID, "") == ("", "unverified")
    assert c.list_network_routers() == [("Casa WiFi", UUID, [R1], R2)]


def test_trusting_a_new_router_after_replacing_it(make):
    c = make().client
    c.set_network_profile("Casa WiFi", UUID, "home")
    c.check_network("Casa WiFi", UUID, R1)
    c.check_network("Casa WiFi", UUID, R2)
    c.trust_router("Casa WiFi", UUID)
    assert c.check_network("Casa WiFi", UUID, R2) == ("home", "known")
    assert c.check_network("Casa WiFi", UUID, R1) == ("home", "known")  # the old one still works
    with pytest.raises(FwError) as exc:  # nothing new is waiting any more
        c.trust_router("Casa WiFi", UUID)
    assert exc.value.code == "no_new_router"


def test_unknown_and_unreadable_input(make):
    c = make().client
    assert c.check_network("Nobody", "", R1) == ("", "unknown")
    c.set_network_profile("Casa WiFi", "", "home")
    assert c.check_network("Casa WiFi", "", "") == ("home", "known")  # no router remembered yet
    for bad in ("not-a-mac", "AA:BB:CC:00:11:22", "00:00:00:00:00:00", "aa:bb:cc:00:11", "x" * 500):
        with pytest.raises(FwError) as exc:
            c.check_network("Casa WiFi", "", bad)
        assert exc.value.code == "invalid_network", bad
    with pytest.raises(FwError) as exc:
        c.trust_router("Nobody", "")
    assert exc.value.code == "unknown_network"


def test_routers_survive_a_restart_and_a_profile_change(make, tmp_path):
    state = tmp_path / "keep"
    d = make(state)
    d.client.set_network_profile("Casa WiFi", UUID, "home")
    d.client.check_network("Casa WiFi", UUID, R1)
    d.client.set_network_profile("Casa WiFi", UUID, "work")  # changing the profile keeps them
    d.stop()
    d2 = make(state)
    assert d2.client.list_network_routers() == [("Casa WiFi", UUID, [R1], "")]
    assert d2.client.check_network("Casa WiFi", UUID, R2) == ("", "changed")


def test_forgetting_the_routers_starts_again(make):
    c = make().client
    c.set_network_profile("Casa WiFi", UUID, "home")
    c.check_network("Casa WiFi", UUID, R1)
    c.forget_routers("Casa WiFi", UUID)
    assert c.list_network_routers() == [("Casa WiFi", UUID, [], "")]
    assert c.check_network("Casa WiFi", UUID, R2) == ("home", "learned")


def test_an_old_networks_file_without_routers_still_loads(make, tmp_path):
    state = tmp_path / "old"
    state.mkdir()
    state.chmod(0o700)
    (state / "networks.json").write_text(
        json.dumps(
            {"version": 1, "networks": [{"ssid": "Casa WiFi", "uuid": UUID, "profile": "home"}]}
        )
    )
    c = make(state).client
    assert c.list_network_routers() == [("Casa WiFi", UUID, [], "")]
    assert c.check_network("Casa WiFi", UUID, R1) == ("home", "learned")


# -- export / import ---


def test_export_then_import_into_another_machine(make, tmp_path):
    a = make(tmp_path / "a").client
    a.set_profile("custom", ["ssh", "web"], True, False, "lan", [])
    a.add_rule("hide", {"profiles": ["work"]})
    a.set_network_profile("Casa WiFi", UUID, "home")
    a.check_network("Casa WiFi", UUID, R1)
    a.set_options(True, "low")
    text = a.export_settings()
    data = json.loads(text)
    assert data["format"] == "fwmanager-settings" and data["networks"][0]["routers"] == [R1]

    b = make(tmp_path / "b").client
    b.import_settings(text)
    assert b.get_profile("custom")["services"] == ["ssh", "web"]
    assert [r[1] for r in b.list_rules()] == ["hide"]
    assert b.list_networks() == [("Casa WiFi", UUID, "home")]
    assert b.list_network_routers()[0][2] == [R1]
    assert b.get_options() == {"router_mode": True, "log_level": "low"}
    assert b.export_settings() == text  # a round trip loses nothing


def test_import_replaces_what_was_there(make, tmp_path):
    c = make().client
    c.set_network_profile("Old", "", "work")
    c.add_rule("hide", {"profiles": ["home"]})
    empty = json.dumps({"format": "fwmanager-settings", "version": 1})
    c.import_settings(empty)
    assert c.list_networks() == [] and c.list_rules() == []
    assert c.get_profile("custom")["customized"] is False


def test_import_survives_a_restart(make, tmp_path):
    state = tmp_path / "keep"
    c = make(state).client
    c.set_network_profile("Casa WiFi", UUID, "home")
    text = c.export_settings()
    c.remove_network("Casa WiFi", UUID)
    c.import_settings(text)
    make_again = make(state)
    assert make_again.client.list_networks() == [("Casa WiFi", UUID, "home")]


def doc(**parts) -> str:
    return json.dumps({"format": "fwmanager-settings", "version": 1, **parts})


def net(**kw) -> dict:
    return {"ssid": "x", "profile": "home", **kw}


BAD_FILES = [
    "",
    "not json",
    "[]",
    json.dumps({"format": "something-else", "version": 1}),
    json.dumps({"format": "fwmanager-settings", "version": 2}),
    doc(profiles={"nope": {}}),
    doc(profiles={"home": {"services": ["nope"]}}),
    doc(profiles={"home": {"services": "ssh"}}),
    doc(profiles={"home": {"ping": "yes"}}),
    doc(profiles={"home": {"source": {"kind": "cidrs", "cidrs": ["1.2.3.4/99"]}}}),
    doc(rules=[{"id": "r1", "template": "nope"}]),
    doc(rules=[{"id": "../x", "template": "hide", "params": {"profiles": ["home"]}}]),
    doc(networks=[net(profile="nope")]),
    doc(networks=[net(routers=["zz"])]),
    doc(networks=[net(), net(profile="work")]),  # the same network twice
    doc(options={"log_level": "loud"}),
    doc(options={"router_mode": "yes"}),
    "x" * 600_000,
    doc(networks=[net(ssid=f"n{i}") for i in range(300)]),
]


@pytest.mark.parametrize("text", BAD_FILES, ids=range(len(BAD_FILES)))
def test_a_bad_file_changes_nothing(make, text):
    c = make().client
    c.set_network_profile("Casa WiFi", UUID, "home")
    c.add_rule("hide", {"profiles": ["work"]})
    before = c.export_settings()
    with pytest.raises(FwError) as exc:
        c.import_settings(text)
    assert exc.value.code in ("invalid_settings", "invalid_input"), exc.value.code
    assert c.export_settings() == before  # not a single field was touched
