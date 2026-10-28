"""The daemon calls the interface relies on: profiles, services, options, events, log."""

import pytest
from helpers.harness import Daemon, busctl_as, ufw_cmd

from fwmanager.errors import FwError

pytestmark = pytest.mark.integration


@pytest.fixture
def make(nft, netns_prefix, private_bus, ufw_root, tmp_path):
    started = []

    def _make():
        d = Daemon(netns_prefix, private_bus, ufw_root, timeout=20, state_dir=tmp_path / "state")
        started.append(d)
        return d

    yield _make
    for d in started:
        d.stop()


def test_list_services_has_titles(daemon):
    services = dict(daemon.client.list_services())
    assert services["ssh"] == "SSH" and "samba" in services and len(services) >= 8


def test_get_profile_defaults(daemon):
    casa = daemon.client.get_profile("home")
    assert casa["name"] == "home" and "ssh" in casa["services"]
    assert casa["ping"] is True and casa["source_kind"] == "lan" and casa["customized"] is False
    assert daemon.client.get_profile("public")["services"] == []


def test_edit_profile_persists_across_restart_and_applies_at_boot(make, ufw_root, netns_prefix):
    d = make()
    d.client.set_profile("work", ["ssh", "web"], True, False, "cidrs", ["10.1.0.0/16"])
    got = d.client.get_profile("work")
    assert got["services"] == ["ssh", "web"] and got["mdns"] is False
    assert got["cidrs"] == ["10.1.0.0/16"] and got["customized"] is True
    d.client.apply_profile("work")
    d.client.confirm()
    assert "10.1.0.0/16" in ufw_cmd(ufw_root, netns_prefix, "show", "added")
    d.stop()
    d2 = make()  # the boot-time apply must use the edited profile, not the factory one
    assert d2.client.get_profile("work")["services"] == ["ssh", "web"]
    added = ufw_cmd(ufw_root, netns_prefix, "show", "added")
    assert "10.1.0.0/16" in added and "80,443" in added


def test_reset_profile(daemon):
    daemon.client.set_profile("home", ["ssh"], False, False, "any")
    assert daemon.client.get_profile("home")["customized"] is True
    daemon.client.reset_profile("home")
    casa = daemon.client.get_profile("home")
    assert casa["customized"] is False and casa["ping"] is True and len(casa["services"]) > 1


@pytest.mark.parametrize(
    "args,code",
    [
        (("nope", [], False, False, "any", []), "unknown_profile"),
        (('x"y', [], False, False, "any", []), "invalid_profile"),
        (("home", ["nope"], False, False, "any", []), "unknown_service"),
        (("home", ['a"b'], False, False, "any", []), "invalid_service"),
        (("home", ["ssh"], False, False, "bogus", []), "invalid_source"),
        (("home", ["ssh"], False, False, "cidrs", []), "invalid_cidr"),
        (("home", ["ssh"], False, False, "cidrs", ["1.2.3.4; reboot"]), "invalid_cidr"),
        (("home", ["ssh"], False, False, "lan", ["10.0.0.0/8"]), "invalid_source"),
        (("home", ["ssh"] * 100, False, False, "any", []), "invalid_service"),
    ],
)
def test_set_profile_validation(daemon, args, code):
    before = daemon.client.get_profile("home")
    with pytest.raises(FwError) as exc:
        daemon.client.set_profile(*args)
    assert exc.value.code == code
    assert daemon.client.get_profile("home") == before
    assert daemon.fp() == daemon.initial


def test_profile_editing_refused_for_outsiders(daemon):
    r = busctl_as(
        65534, daemon.bus, "SetProfile", "sasbbsas", "home", "0", "false", "false", "any", "0"
    )
    assert r.returncode != 0 and "not in the fwmanager group" in r.stderr
    assert daemon.client.get_profile("home")["customized"] is False


def test_options_default_persist_and_take_effect(make, ufw_root):
    d = make()
    assert d.client.get_options() == {"router_mode": False, "log_level": ""}
    d.client.set_options(True, "medium")
    assert d.client.get_options() == {"router_mode": True, "log_level": "medium"}
    d.client.apply_profile("public")
    d.client.confirm()
    assert 'DEFAULT_FORWARD_POLICY="DROP"' in (ufw_root / "etc/default/ufw").read_text()
    assert "LOGLEVEL=medium" in (ufw_root / "etc/ufw/ufw.conf").read_text()
    d.stop()
    d2 = make()
    assert d2.client.get_options()["router_mode"] is True


@pytest.mark.parametrize("level", ["loud", "HIGH", 'low"; ufw reset'])
def test_options_validation(daemon, level):
    with pytest.raises(FwError) as exc:
        daemon.client.set_options(False, level)
    assert exc.value.code == "invalid_option"
    assert daemon.client.get_options()["log_level"] == ""


def test_events_record_what_happened(daemon):
    daemon.client.apply_profile("home")
    daemon.client.confirm()
    daemon.client.set_profile("home", ["ssh"], True, True, "lan")
    kinds = [(k, d) for _, k, d in daemon.client.get_events()]
    assert ("startup", "first_boot") in kinds
    assert ("applied", "home") in kinds and ("confirmed", "home") in kinds
    assert ("profile_edited", "home") in kinds
    stamps = [t for t, _, _ in daemon.client.get_events()]
    assert stamps == sorted(stamps) and all(t > 1_700_000_000 for t in stamps)


def test_events_contain_no_rule_details(daemon):
    daemon.client.apply_profile("home")
    daemon.client.rollback()
    text = " ".join(f"{k} {d}" for _, k, d in daemon.client.get_events())
    assert "192.168" not in text and "port" not in text
    assert ("reverted", "home") in [(k, d) for _, k, d in daemon.client.get_events()]


def test_denied_calls_are_recorded_without_a_name(daemon):
    busctl_as(65534, daemon.bus, "GetStatus")
    assert ("denied", "") in [(k, d) for _, k, d in daemon.client.get_events()]


def test_recent_blocked_is_a_safe_list(daemon):
    items = daemon.client.get_recent_blocked(20)
    assert isinstance(items, list) and all(len(i) == 6 for i in items)
    assert len(daemon.client.get_recent_blocked(4_000_000_000)) <= 500  # clamped, not an error


def test_recent_allowed_is_a_safe_list_too(daemon):
    items = daemon.client.get_recent_allowed(20)
    assert isinstance(items, list) and all(len(i) == 6 for i in items)
    assert len(daemon.client.get_recent_allowed(4_000_000_000)) <= 500
