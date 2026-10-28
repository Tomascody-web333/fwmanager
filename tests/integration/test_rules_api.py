"""Plain-language rules through the real daemon and ufw."""

import json

import pytest
from helpers.harness import Daemon, busctl_as, ufw_cmd

from fwmanager.core import rulebook as rb
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


def allow_web_only_in_casa(client):
    return client.add_rule(rb.ALLOW_ONLY_IN, {"service": "web", "profiles": ["home"]})


def test_no_rules_at_first(daemon):
    assert daemon.client.list_rules() == []


def test_add_list_and_remove(daemon):
    rid = allow_web_only_in_casa(daemon.client)
    assert rid == "r1"
    ((got_id, template, params),) = daemon.client.list_rules()
    assert (got_id, template) == ("r1", "allow_only_in")
    assert params == {"service": "web", "profiles": ["home"]}
    assert allow_web_only_in_casa(daemon.client) == "r2"
    daemon.client.remove_rule("r1")
    assert [r[0] for r in daemon.client.list_rules()] == ["r2"]
    assert allow_web_only_in_casa(daemon.client) == "r1"  # the freed id is reused


def test_rules_change_the_profiles_the_ui_sees(daemon):
    allow_web_only_in_casa(daemon.client)
    casa = daemon.client.get_profile("home")
    assert "web" in casa["services"] and casa["affected_by_rules"] is True
    assert casa["customized"] is False  # a rule is not a hand edit
    trabalho = daemon.client.get_profile("work")
    assert "web" not in trabalho["services"] and trabalho["affected_by_rules"] is False
    daemon.client.remove_rule("r1")
    assert "web" not in daemon.client.get_profile("home")["services"]


def test_saving_a_rule_does_not_touch_the_firewall(daemon):
    allow_web_only_in_casa(daemon.client)
    daemon.client.add_rule(rb.HIDE, {"profiles": ["home"]})
    assert daemon.fp() == daemon.initial  # nothing applied until a profile is applied


def test_applying_uses_the_rules(daemon, ufw_root, netns_prefix):
    allow_web_only_in_casa(daemon.client)
    daemon.client.add_rule(rb.CLOSE_SERVICE, {"service": "samba"})
    daemon.client.apply_profile("home")
    daemon.client.confirm()
    added = ufw_cmd(ufw_root, netns_prefix, "show", "added")
    assert "80,443" in added and "445" not in added and "139" not in added
    daemon.client.apply_profile("work")
    daemon.client.confirm()
    assert "80,443" not in ufw_cmd(ufw_root, netns_prefix, "show", "added")


def test_hide_rule_reaches_before_rules(daemon, ufw_root):
    daemon.client.add_rule(rb.HIDE, {"profiles": ["home"]})
    daemon.client.apply_profile("home")
    daemon.client.confirm()
    text = (ufw_root / "etc/ufw/before.rules").read_text()
    assert "echo-request -j DROP" in text and "224.0.0.251 --dport 5353 -j DROP" in text


def test_preview_describes_changes_without_saving(daemon):
    changes = daemon.client.preview_rule(
        rb.ALLOW_ONLY_IN, {"service": "samba", "profiles": ["work"]}
    )
    by_name = {c[0]: c for c in changes}
    assert by_name["work"][1] == ["samba"] and by_name["work"][2] == []
    assert by_name["home"][2] == ["samba"] and by_name["home"][1] == []
    assert daemon.client.list_rules() == []  # a preview saves nothing
    assert daemon.fp() == daemon.initial


def test_preview_of_ping_mdns_and_source_changes(daemon):
    (change,) = daemon.client.preview_rule(rb.HIDE, {"profiles": ["home"]})
    assert change[0] == "home" and change[3] == "off" and change[4] == "off" and change[5] == ""
    (change,) = daemon.client.preview_rule(
        rb.RESTRICT_SOURCE, {"scope": "cidrs", "cidrs": ["10.9.0.0/16"], "profiles": ["public"]}
    )
    assert change[0] == "public" and change[5] == "cidrs" and change[6] == ["10.9.0.0/16"]


def test_preview_of_a_noop_is_empty(daemon):
    assert daemon.client.preview_rule(rb.HIDE, {"profiles": ["public"]}) == []


def test_preview_takes_earlier_rules_into_account(daemon):
    daemon.client.add_rule(rb.BLOCK_ALL_EXCEPT, {"services": ["ssh"], "profiles": ["home"]})
    changes = {c[0]: c for c in daemon.client.preview_rule(rb.CLOSE_SERVICE, {"service": "ssh"})}
    assert changes["home"][2] == ["ssh"]  # it was open only because of the first rule


@pytest.mark.parametrize(
    "template,params,code",
    [
        ("nope", {}, "unknown_template"),
        (rb.ALLOW_ONLY_IN, {"service": "nope", "profiles": ["home"]}, "unknown_service"),
        (rb.ALLOW_ONLY_IN, {"service": "ssh", "profiles": ["nope"]}, "unknown_profile"),
        (rb.ALLOW_ONLY_IN, {"service": 'ssh"; ufw reset', "profiles": ["home"]}, "invalid_service"),
        (rb.ALLOW_ONLY_IN, {"service": "ssh", "profiles": []}, "invalid_rule"),
        (rb.RESTRICT_SOURCE, {"scope": "cidrs", "cidrs": ["1.2.3.4; ls"], "profiles": ["home"]},
         "invalid_cidr"),
        (rb.HIDE, {"profiles": ["home"], "extra": ["x"]}, "invalid_rule"),
    ],
)  # fmt: skip
def test_invalid_rules_are_rejected_everywhere(daemon, template, params, code):
    for call in (daemon.client.add_rule, daemon.client.preview_rule):
        with pytest.raises(FwError) as exc:
            call(template, params)
        assert exc.value.code == code
    assert daemon.client.list_rules() == [] and daemon.fp() == daemon.initial


@pytest.mark.parametrize("rid", ["nope", "R1", 'r1"; x', "", "x" * 30])
def test_remove_unknown_rule(daemon, rid):
    with pytest.raises(FwError) as exc:
        daemon.client.remove_rule(rid)
    assert exc.value.code == "unknown_rule"


def test_rule_limit(daemon):
    for _ in range(rb.MAX_RULES):
        daemon.client.add_rule(rb.HIDE, {"profiles": ["home"]})
    with pytest.raises(FwError) as exc:
        daemon.client.add_rule(rb.HIDE, {"profiles": ["home"]})
    assert exc.value.code == "too_many_rules"


def test_rules_persist_and_apply_at_boot(make, ufw_root, netns_prefix):
    d = make()
    allow_web_only_in_casa(d.client)
    d.client.add_rule(rb.CLOSE_SERVICE, {"service": "samba"})
    d.client.apply_profile("home")
    d.client.confirm()
    d.stop()
    d2 = make()  # boot applies the confirmed profile *with* the rules
    assert [r[1] for r in d2.client.list_rules()] == ["allow_only_in", "close_service"]
    added = ufw_cmd(ufw_root, netns_prefix, "show", "added")
    assert "80,443" in added and "445" not in added


def test_rules_sit_on_top_of_hand_edits_and_survive_them(daemon):
    daemon.client.set_profile("work", ["ssh", "samba"], True, False, "lan")
    daemon.client.add_rule(rb.CLOSE_SERVICE, {"service": "samba"})
    assert daemon.client.get_profile("work")["services"] == ["ssh"]
    daemon.client.remove_rule("r1")
    assert daemon.client.get_profile("work")["services"] == ["ssh", "samba"]  # edit is back
    daemon.client.add_rule(rb.HIDE, {"profiles": ["work"]})
    daemon.client.reset_profile("work")  # resets the edit, the rule still applies
    got = daemon.client.get_profile("work")
    assert got["services"] == ["ssh"] and got["ping"] is False and got["customized"] is False


def test_editing_a_profile_keeps_the_rules_out_of_the_saved_edit(make, tmp_path):
    d = make()
    d.client.add_rule(rb.HIDE, {"profiles": ["home"]})
    d.client.set_profile("home", ["ssh"], True, True, "lan")  # the user asks ping on...
    assert d.client.get_profile("home")["ping"] is False  # ...but the rule still hides it
    saved = json.loads((tmp_path / "state" / "profiles.json").read_text())
    assert saved["profiles"]["home"]["ping"] is True  # the edit itself is stored untouched


def test_damaged_or_stale_rules_are_ignored_not_fatal(make, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "rules.json").write_text("{broken")
    d = make()
    assert d.client.list_rules() == [] and d.client.status()["active_profile"] == "public"


def test_a_rule_naming_a_vanished_service_is_dropped(make, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "rules.json").write_text(
        json.dumps({"version": 1, "rules": [
            {"id": "r1", "template": "close_service", "params": {"service": "gone"}},
            {"id": "r2", "template": "hide", "params": {"profiles": ["home"]}},
        ]})
    )  # fmt: skip
    d = make()
    assert [r[0] for r in d.client.list_rules()] == ["r2"]


def test_rule_events_and_outsiders(daemon):
    allow_web_only_in_casa(daemon.client)
    daemon.client.remove_rule("r1")
    kinds = [(k, d) for _, k, d in daemon.client.get_events()]
    assert ("rule_added", "r1") in kinds and ("rule_removed", "r1") in kinds
    r = busctl_as(65534, daemon.bus, "RemoveRule", "s", "r1")
    assert r.returncode != 0 and "not in the fwmanager group" in r.stderr
    r = busctl_as(65534, daemon.bus, "ListRules")
    assert r.returncode != 0


def test_profiles_too_big_to_apply_quickly_are_refused(daemon):
    import time

    from fwmanager.core.ufw_rules import MAX_RULES_PER_PROFILE

    c = daemon.client
    services = [s for s, _ in c.list_services()]
    many = [f"10.{i}.0.0/16" for i in range(64)]
    for call in (
        lambda: c.set_profile("custom", services, True, True, "cidrs", many),
        lambda: c.add_rule(
            rb.RESTRICT_SOURCE, {"scope": "cidrs", "cidrs": many, "profiles": ["home"]}
        ),
        lambda: c.preview_rule(
            rb.RESTRICT_SOURCE, {"scope": "cidrs", "cidrs": many, "profiles": ["home"]}
        ),
    ):
        with pytest.raises(FwError) as exc:
            call()
        assert exc.value.code == "profile_too_big"
    assert c.list_rules() == [] and c.get_profile("custom")["cidrs"] == []
    # The largest allowed profile applies well within the client's patience.
    c.set_profile("custom", ["ssh"], True, True, "cidrs", many[:MAX_RULES_PER_PROFILE])
    start = time.monotonic()
    c.apply_profile("custom")
    assert time.monotonic() - start < 20
    c.rollback()
    assert daemon.fp() == daemon.initial
