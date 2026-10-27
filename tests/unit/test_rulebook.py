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
    out = compiled(rule(rb.ALLOW_ONLY_IN, service="samba", profiles=["trabalho"]))
    assert "samba" in out["trabalho"].services
    assert "samba" not in out["casa"].services  # casa had it by default
    assert out["publico"].services == ()


def test_allow_only_in_does_not_duplicate_or_reorder_the_rest():
    out = compiled(rule(rb.ALLOW_ONLY_IN, service="ssh", profiles=["casa", "trabalho"]))
    assert out["casa"].services.count("ssh") == 1
    assert out["personalizado"].services == ()  # it had ssh, but is not listed


def test_block_all_except_replaces_the_services():
    out = compiled(rule(rb.BLOCK_ALL_EXCEPT, services=["ssh"], profiles=["casa"]))
    assert out["casa"].services == ("ssh",)
    assert out["trabalho"] == DEFAULT_PROFILES["trabalho"]  # not listed: untouched


def test_block_everything_with_no_exceptions():
    out = compiled(rule(rb.BLOCK_ALL_EXCEPT, services=[], profiles=["casa", "trabalho"]))
    assert out["casa"].services == () and out["trabalho"].services == ()


def test_restrict_source_to_the_local_network_and_to_cidrs():
    out = compiled(rule(rb.RESTRICT_SOURCE, scope="lan", profiles=["publico"]))
    assert out["publico"].source == Source("lan")
    out = compiled(
        rule(rb.RESTRICT_SOURCE, scope="cidrs", cidrs=["10.1.0.0/16"], profiles=["casa"])
    )
    assert out["casa"].source == Source("cidrs", ("10.1.0.0/16",))


def test_hide_turns_off_ping_and_mdns():
    out = compiled(rule(rb.HIDE, profiles=["casa"]))
    assert not out["casa"].ping and not out["casa"].mdns
    assert out["trabalho"].ping  # untouched


def test_close_service_everywhere():
    out = compiled(rule(rb.CLOSE_SERVICE, service="ssh"))
    assert all("ssh" not in p.services for p in out.values())
    assert "samba" in out["casa"].services


def test_no_rules_means_unchanged_profiles():
    assert compiled() == dict(DEFAULT_PROFILES)


def test_later_rules_win_and_apply_in_order():
    first = rule(rb.BLOCK_ALL_EXCEPT, "r1", services=["ssh"], profiles=["casa"])
    second = rule(rb.ALLOW_ONLY_IN, "r2", service="web", profiles=["casa"])
    out = compiled(first, second)
    assert out["casa"].services == ("ssh", "web")
    reordered = compiled(second, first)
    assert reordered["casa"].services == ("ssh",)  # the block, being later, wins


def test_compiled_profiles_stay_valid():
    out = compiled(
        rule(rb.ALLOW_ONLY_IN, "r1", service="web", profiles=["casa"]),
        rule(rb.RESTRICT_SOURCE, "r2", scope="cidrs", cidrs=["192.168.1.0/24"], profiles=["casa"]),
    )
    from fwmanager.core.ufw_rules import translate

    assert translate(out["casa"], CAT)  # translates to ufw rules without error


# -- validation ---


@pytest.mark.parametrize(
    "template,params,code",
    [
        ("nope", {}, "unknown_template"),
        (rb.ALLOW_ONLY_IN, {"service": "nope", "profiles": ["casa"]}, "unknown_service"),
        (rb.ALLOW_ONLY_IN, {"service": 'ssh"; ufw x', "profiles": ["casa"]}, "invalid_service"),
        (rb.ALLOW_ONLY_IN, {"service": "ssh", "profiles": []}, "invalid_rule"),
        (rb.ALLOW_ONLY_IN, {"service": "ssh", "profiles": ["nope"]}, "unknown_profile"),
        (rb.ALLOW_ONLY_IN, {"service": "ssh", "profiles": ["CASA"]}, "invalid_profile"),
        (rb.ALLOW_ONLY_IN, {"service": "ssh", "profiles": "casa"}, "invalid_rule"),
        (rb.ALLOW_ONLY_IN, {"service": "ssh"}, "invalid_rule"),
        (rb.ALLOW_ONLY_IN, {"service": "ssh", "profiles": ["casa"], "extra": 1}, "invalid_rule"),
        (rb.BLOCK_ALL_EXCEPT, {"services": ["ssh", "no"], "profiles": ["casa"]}, "unknown_service"),
        (rb.BLOCK_ALL_EXCEPT, {"services": "ssh", "profiles": ["casa"]}, "invalid_rule"),
        (rb.RESTRICT_SOURCE, {"scope": "world", "profiles": ["casa"]}, "invalid_source"),
        (rb.RESTRICT_SOURCE, {"scope": "cidrs", "profiles": ["casa"]}, "invalid_rule"),
        (rb.RESTRICT_SOURCE, {"scope": "cidrs", "cidrs": [], "profiles": ["casa"]}, "invalid_cidr"),
        (rb.RESTRICT_SOURCE, {"scope": "cidrs", "cidrs": ["1.2.3.4; reboot"], "profiles": ["casa"]},
         "invalid_cidr"),
        (rb.RESTRICT_SOURCE, {"scope": "cidrs", "cidrs": "10.0.0.0/8", "profiles": ["casa"]},
         "invalid_cidr"),
        (rb.RESTRICT_SOURCE, {"scope": "lan", "cidrs": ["10.0.0.0/8"], "profiles": ["casa"]},
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
        rb.BLOCK_ALL_EXCEPT, {"services": ["ssh", "ssh", "web"], "profiles": ["casa", "casa"]},
        CAT, NAMES,
    )  # fmt: skip
    assert clean == {"services": ("ssh", "web"), "profiles": ("casa",)}
    cidrs = rb.validate_params(
        rb.RESTRICT_SOURCE, {"scope": "cidrs", "cidrs": ["10.0.0.1"], "profiles": ["casa"]},
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
    after = compiled(rule(rb.ALLOW_ONLY_IN, service="samba", profiles=["trabalho"]))
    changes = {c.name: c for c in rb.diff_profiles(DEFAULT_PROFILES, after)}
    assert set(changes) == {"trabalho", "casa"}
    assert changes["trabalho"].opened == ("samba",) and changes["trabalho"].closed == ()
    assert changes["casa"].closed == ("samba",) and changes["casa"].ping is None


def test_diff_reports_ping_mdns_and_source_changes():
    after = compiled(
        rule(rb.HIDE, "r1", profiles=["casa"]),
        rule(rb.RESTRICT_SOURCE, "r2", scope="cidrs", cidrs=["10.0.0.0/8"], profiles=["casa"]),
    )
    (change,) = rb.diff_profiles(DEFAULT_PROFILES, after)
    assert change.ping is False and change.mdns is False
    assert change.source == Source("cidrs", ("10.0.0.0/8",))


def test_diff_of_a_noop_is_empty():
    after = compiled(rule(rb.HIDE, profiles=["publico"]))  # publico is already hidden
    assert rb.diff_profiles(DEFAULT_PROFILES, after) == []


def test_apply_rule_is_pure():
    before = DEFAULT_PROFILES["casa"]
    r = rule(rb.CLOSE_SERVICE, service="ssh")
    rb.apply_rule(r, before)
    assert before == Profile("casa", ("ssh", "samba", "kdeconnect", "syncthing", "ipp"),
                             True, True, Source("lan"))  # fmt: skip
