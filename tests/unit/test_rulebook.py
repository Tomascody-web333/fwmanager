from dataclasses import replace

import pytest

from fwmanager.core import rulebook as rb
from fwmanager.core.profiles import DEFAULT_PROFILES, Profile, Source
from fwmanager.core.services import load_builtin
from fwmanager.errors import ValidationError

CAT = load_builtin()
NAMES = frozenset(DEFAULT_PROFILES)


def rule(template, rid="r1", **params):
    return rb.Rule(rid, template, rb.validate_params(template, params, CAT, NAMES))


def compiled(*rules):
    return rb.compile_profiles(DEFAULT_PROFILES, list(rules))


# -- the five templates ---


def test_allow_only_in_adds_there_and_removes_elsewhere():
    out = compiled(rule(rb.ALLOW_ONLY_IN, service="samba", profiles=["work"]))
    assert "samba" in out["work"].services
    assert "samba" not in out["home"].services  # casa had it by default
    assert out["public"].services == ()


def test_allow_only_in_does_not_duplicate_or_reorder_the_rest():
    out = compiled(rule(rb.ALLOW_ONLY_IN, service="ssh", profiles=["home", "work"]))
    assert out["home"].services.count("ssh") == 1
    assert out["custom"].services == ()  # it had ssh, but is not listed


def test_block_all_except_replaces_the_services():
    out = compiled(rule(rb.BLOCK_ALL_EXCEPT, services=["ssh"], profiles=["home"]))
    assert out["home"].services == ("ssh",)
    assert out["work"] == DEFAULT_PROFILES["work"]  # not listed: untouched


def test_block_everything_with_no_exceptions():
    out = compiled(rule(rb.BLOCK_ALL_EXCEPT, services=[], profiles=["home", "work"]))
    assert out["home"].services == () and out["work"].services == ()


def test_restrict_source_to_the_local_network_and_to_cidrs():
    out = compiled(rule(rb.RESTRICT_SOURCE, scope="lan", profiles=["public"]))
    assert out["public"].source == Source("lan")
    out = compiled(
        rule(rb.RESTRICT_SOURCE, scope="cidrs", cidrs=["10.1.0.0/16"], profiles=["home"])
    )
    assert out["home"].source == Source("cidrs", ("10.1.0.0/16",))


def test_hide_turns_off_ping_and_mdns():
    out = compiled(rule(rb.HIDE, profiles=["home"]))
    assert not out["home"].ping and not out["home"].mdns
    assert out["work"].ping  # untouched


def test_close_service_everywhere():
    out = compiled(rule(rb.CLOSE_SERVICE, service="ssh"))
    assert all("ssh" not in p.services for p in out.values())
    assert "samba" in out["home"].services


def test_no_rules_means_unchanged_profiles():
    assert compiled() == dict(DEFAULT_PROFILES)


def test_later_rules_win_and_apply_in_order():
    first = rule(rb.BLOCK_ALL_EXCEPT, "r1", services=["ssh"], profiles=["home"])
    second = rule(rb.ALLOW_ONLY_IN, "r2", service="web", profiles=["home"])
    out = compiled(first, second)
    assert out["home"].services == ("ssh", "web")
    reordered = compiled(second, first)
    assert reordered["home"].services == ("ssh",)  # the block, being later, wins


def test_compiled_profiles_stay_valid():
    out = compiled(
        rule(rb.ALLOW_ONLY_IN, "r1", service="web", profiles=["home"]),
        rule(rb.RESTRICT_SOURCE, "r2", scope="cidrs", cidrs=["192.168.1.0/24"], profiles=["home"]),
    )
    from fwmanager.core.ufw_rules import translate

    assert translate(out["home"], CAT)  # translates to ufw rules without error


# -- validation ---


@pytest.mark.parametrize(
    "template,params,code",
    [
        ("nope", {}, "unknown_template"),
        (rb.ALLOW_ONLY_IN, {"service": "nope", "profiles": ["home"]}, "unknown_service"),
        (rb.ALLOW_ONLY_IN, {"service": 'ssh"; ufw x', "profiles": ["home"]}, "invalid_service"),
        (rb.ALLOW_ONLY_IN, {"service": "ssh", "profiles": []}, "invalid_rule"),
        (rb.ALLOW_ONLY_IN, {"service": "ssh", "profiles": ["nope"]}, "unknown_profile"),
        (rb.ALLOW_ONLY_IN, {"service": "ssh", "profiles": ["CASA"]}, "invalid_profile"),
        (rb.ALLOW_ONLY_IN, {"service": "ssh", "profiles": "home"}, "invalid_rule"),
        (rb.ALLOW_ONLY_IN, {"service": "ssh"}, "invalid_rule"),
        (rb.ALLOW_ONLY_IN, {"service": "ssh", "profiles": ["home"], "extra": 1}, "invalid_rule"),
        (rb.BLOCK_ALL_EXCEPT, {"services": ["ssh", "no"], "profiles": ["home"]}, "unknown_service"),
        (rb.BLOCK_ALL_EXCEPT, {"services": "ssh", "profiles": ["home"]}, "invalid_rule"),
        (rb.RESTRICT_SOURCE, {"scope": "world", "profiles": ["home"]}, "invalid_source"),
        (rb.RESTRICT_SOURCE, {"scope": "cidrs", "profiles": ["home"]}, "invalid_rule"),
        (rb.RESTRICT_SOURCE, {"scope": "cidrs", "cidrs": [], "profiles": ["home"]}, "invalid_cidr"),
        (rb.RESTRICT_SOURCE, {"scope": "cidrs", "cidrs": ["1.2.3.4; reboot"], "profiles": ["home"]},
         "invalid_cidr"),
        (rb.RESTRICT_SOURCE, {"scope": "cidrs", "cidrs": "10.0.0.0/8", "profiles": ["home"]},
         "invalid_cidr"),
        (rb.RESTRICT_SOURCE, {"scope": "lan", "cidrs": ["10.0.0.0/8"], "profiles": ["home"]},
         "invalid_rule"),
        (rb.HIDE, {}, "invalid_rule"),
        (rb.CLOSE_SERVICE, {"service": None}, "invalid_service"),
    ],
)  # fmt: skip
def test_validation_rejects(template, params, code):
    with pytest.raises(ValidationError) as exc:
        rb.validate_params(template, params, CAT, NAMES)
    assert exc.value.code == code


def test_validation_normalises():
    clean = rb.validate_params(
        rb.BLOCK_ALL_EXCEPT, {"services": ["ssh", "ssh", "web"], "profiles": ["home", "home"]},
        CAT, NAMES,
    )  # fmt: skip
    assert clean == {"services": ("ssh", "web"), "profiles": ("home",)}
    cidrs = rb.validate_params(
        rb.RESTRICT_SOURCE, {"scope": "cidrs", "cidrs": ["10.0.0.1"], "profiles": ["home"]},
        CAT, NAMES,
    )  # fmt: skip
    assert cidrs["cidrs"] == ("10.0.0.1/32",)


@pytest.mark.parametrize("bad", ["", "R1", "a b", "x" * 17, 'r"1', None])
def test_rule_ids_are_strict(bad):
    with pytest.raises(ValidationError):
        rb.Rule(bad, rb.HIDE, {})


def test_next_rule_id_fills_gaps():
    assert rb.next_rule_id([]) == "r1"
    assert rb.next_rule_id([rb.Rule("r1", rb.HIDE), rb.Rule("r3", rb.HIDE)]) == "r2"


# -- preview diff ---


def test_diff_lists_only_what_changes():
    after = compiled(rule(rb.ALLOW_ONLY_IN, service="samba", profiles=["work"]))
    changes = {c.name: c for c in rb.diff_profiles(DEFAULT_PROFILES, after)}
    assert set(changes) == {"work", "home"}
    assert changes["work"].opened == ("samba",) and changes["work"].closed == ()
    assert changes["home"].closed == ("samba",) and changes["home"].ping is None


def test_diff_reports_ping_mdns_and_source_changes():
    after = compiled(
        rule(rb.HIDE, "r1", profiles=["home"]),
        rule(rb.RESTRICT_SOURCE, "r2", scope="cidrs", cidrs=["10.0.0.0/8"], profiles=["home"]),
    )
    (change,) = rb.diff_profiles(DEFAULT_PROFILES, after)
    assert change.ping is False and change.mdns is False
    assert change.source == Source("cidrs", ("10.0.0.0/8",))


def test_diff_of_a_noop_is_empty():
    after = compiled(rule(rb.HIDE, profiles=["public"]))  # publico is already hidden
    assert rb.diff_profiles(DEFAULT_PROFILES, after) == []


def test_apply_rule_is_pure():
    before = DEFAULT_PROFILES["home"]
    r = rule(rb.CLOSE_SERVICE, service="ssh")
    rb.apply_rule(r, before)
    assert before == Profile("home", ("ssh", "samba", "kdeconnect", "syncthing", "ipp"),
                             True, True, Source("lan"))  # fmt: skip


def test_a_rule_that_changes_nothing_reports_nothing():
    """ "Allow Samba only in Home" when Home already has Samba: no empty "Home:" preview."""
    cat = load_builtin()
    names = frozenset(DEFAULT_PROFILES)
    for template, params in (
        (rb.ALLOW_ONLY_IN, {"service": "samba", "profiles": ["home"]}),
        (rb.BLOCK_ALL_EXCEPT, {"services": ["ipp", "ssh", "samba", "kdeconnect", "syncthing"],
                               "profiles": ["home"]}),  # same set as Home, other order
    ):  # fmt: skip
        rule = rb.Rule("r1", template, rb.validate_params(template, params, cat, names))
        after = rb.compile_profiles(DEFAULT_PROFILES, [rule])
        assert after["home"] == DEFAULT_PROFILES["home"]  # not even reordered
        assert rb.diff_profiles(DEFAULT_PROFILES, after) == []


def test_same_effect_ignores_the_order_of_services():
    a = DEFAULT_PROFILES["home"]
    b = replace(a, services=tuple(reversed(a.services)))
    assert a != b and rb.same_effect(a, b)
    assert not rb.same_effect(a, replace(a, ping=not a.ping))
