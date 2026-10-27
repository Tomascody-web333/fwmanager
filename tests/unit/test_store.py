import json
import os

import pytest

from fwmanager.core.store import MAX_NETWORKS, Network, Store, find_profile
from fwmanager.errors import StoreCorrupt, StoreError, ValidationError

UUID = "123e4567-e89b-12d3-a456-426614174000"


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "state")


def test_state_roundtrip_and_missing(store):
    assert store.load_state() is None
    store.save_state("casa")
    assert store.load_state() == "casa"
    store.save_state("publico")
    assert store.load_state() == "publico"
    assert not list(store._dir.glob(".*.tmp"))  # no temp leftovers


@pytest.mark.parametrize(
    "content",
    [
        "",
        "{",
        "[]",
        "null",
        '{"version": 2, "profile": "casa"}',
        '{"version": 1}',
        '{"version": 1, "profile": "x\\ny"}',
        '{"version": 1, "profile": 5}',
        "\x00\xff",
    ],
)
def test_corrupt_state(store, content):
    store._dir.mkdir()
    (store._dir / "state.json").write_bytes(content.encode("latin-1"))
    with pytest.raises(StoreCorrupt):
        store.load_state()


def test_oversized_state_is_corrupt(store):
    store._dir.mkdir()
    (store._dir / "state.json").write_text(" " * (300 * 1024))
    with pytest.raises(StoreCorrupt):
        store.load_state()


def test_quarantine_keeps_the_bad_file(store):
    store._dir.mkdir()
    (store._dir / "state.json").write_text("garbage")
    store.quarantine_state()
    assert (store._dir / "state.json.corrupt").read_text() == "garbage"
    assert store.load_state() is None


def test_save_rejects_bad_profile(store):
    with pytest.raises(ValidationError):
        store.save_state('a"b')


def test_write_failure_is_a_store_error(tmp_path):
    f = tmp_path / "file"
    f.write_text("x")
    with pytest.raises(StoreError):
        Store(f / "sub").save_state("casa")  # parent is a file


def test_state_file_permissions(store):
    store.save_state("casa")
    assert (os.stat(store._dir / "state.json").st_mode & 0o777) == 0o644


def test_networks_roundtrip(store):
    nets = [Network("Casa WiFi", UUID, "casa"), Network("Café ☕", "", "publico")]
    store.save_networks(nets)
    assert store.load_networks() == nets
    assert Store(store._dir).load_networks() == nets


def test_networks_missing_is_empty(store):
    assert store.load_networks() == []


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(ssid="", uuid="", profile="casa"),
        dict(ssid="a\nb", uuid="", profile="casa"),
        dict(ssid="x" * 33, uuid="", profile="casa"),
        dict(ssid="ç" * 17, uuid="", profile="casa"),  # 34 bytes
        dict(ssid="ok", uuid="not-a-uuid", profile="casa"),
        dict(ssid="ok", uuid=UUID.upper(), profile="casa"),
        dict(ssid="ok", uuid=UUID.replace("-", ""), profile="casa"),
        dict(ssid="ok", uuid="", profile='x"y'),
        dict(ssid="ok", uuid="", profile="Casa"),
    ],
)
def test_network_validation(kwargs):
    with pytest.raises(ValidationError):
        Network(**kwargs)


def test_networks_limit(store):
    many = [Network(f"n{i}", "", "casa") for i in range(MAX_NETWORKS + 1)]
    with pytest.raises(ValidationError):
        store.save_networks(many)


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 1, "networks": "x"},
        {"version": 1, "networks": [{"ssid": "a", "profile": "BAD NAME"}]},
        {"version": 1, "networks": [{"profile": "casa"}]},
        {"version": 1, "networks": ["str"]},
        {"version": 1, "networks": [{"ssid": "a", "uuid": "zzz", "profile": "casa"}]},
    ],
)
def test_corrupt_networks(store, payload):
    store._dir.mkdir()
    (store._dir / "networks.json").write_text(json.dumps(payload))
    with pytest.raises(StoreCorrupt):
        store.load_networks()


def test_find_profile_prefers_uuid():
    nets = [Network("Cafe", "", "publico"), Network("Cafe", UUID, "casa")]
    assert find_profile(nets, "Cafe", UUID) == "casa"
    assert find_profile(nets, "Cafe", "") == "publico"
    assert find_profile(nets, "Other", "") is None
    assert find_profile(nets, "", "") is None


def test_same_ssid_different_uuid_does_not_match():
    nets = [Network("Cafe", UUID, "casa")]
    other = "223e4567-e89b-12d3-a456-426614174000"
    assert find_profile(nets, "Cafe", other) is None  # a different connection


# -- edited profiles and options ---


def test_profiles_roundtrip(store):
    from fwmanager.core.profiles import Profile, Source

    custom = {
        "casa": Profile("casa", ("ssh", "web"), True, False, Source("cidrs", ("10.1.0.0/16",))),
        "publico": Profile("publico", (), False, False, Source("any")),
    }
    store.save_profiles(custom)
    assert store.load_profiles() == custom
    assert Store(store._dir).load_profiles() == custom


def test_profiles_missing_is_empty(store):
    assert store.load_profiles() == {}


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 1, "profiles": "x"},
        {"version": 1, "profiles": {"casa": {}}},
        {"version": 1, "profiles": {"CASA": {"services": [], "ping": 1, "mdns": 0,
                                              "source": {"kind": "any"}}}},
        {"version": 1, "profiles": {"casa": {"services": ["a b"], "ping": 1, "mdns": 0,
                                              "source": {"kind": "any"}}}},
        {"version": 1, "profiles": {"casa": {"services": [], "ping": 1, "mdns": 0,
                                              "source": {"kind": "bogus"}}}},
        {"version": 1, "profiles": {"casa": {"services": [], "ping": 1, "mdns": 0,
                                              "source": {"kind": "any", "cidrs": ["1.2.3.4"]}}}},
    ],
)  # fmt: skip
def test_corrupt_profiles(store, payload):
    store._dir.mkdir()
    (store._dir / "profiles.json").write_text(json.dumps(payload))
    with pytest.raises(StoreCorrupt):
        store.load_profiles()


def test_options_roundtrip_and_defaults(store):
    from fwmanager.core.backend import Options

    assert store.load_options() == Options()
    store.save_options(Options(router_mode=True, log_level="medium"))
    assert store.load_options() == Options(router_mode=True, log_level="medium")


def test_corrupt_options(store):
    store._dir.mkdir()
    (store._dir / "options.json").write_text('{"version": 1, "log_level": "loud"}')
    with pytest.raises(StoreCorrupt):
        store.load_options()


# -- plain-language rules ---


def test_rules_roundtrip_keeps_lists(store):
    from fwmanager.core.rulebook import Rule

    rules = [
        Rule("r1", "allow_only_in", {"service": "web", "profiles": ("casa", "trabalho")}),
        Rule("r2", "hide", {"profiles": ("publico",)}),
    ]
    store.save_rules(rules)
    loaded = store.load_rules()
    assert [r.id for r in loaded] == ["r1", "r2"]
    assert loaded[0].params == {"service": "web", "profiles": ["casa", "trabalho"]}
    assert (store._dir / "rules.json").exists()


def test_rules_missing_is_empty(store):
    assert store.load_rules() == []


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 1, "rules": "x"},
        {"version": 1, "rules": [{"id": "r1"}]},
        {"version": 1, "rules": [{"id": "R 1", "template": "hide", "params": {}}]},
        {"version": 1, "rules": [{"id": "r1", "template": "hide", "params": "x"}]},
        {"version": 1, "rules": [{"id": "r1", "template": "hide", "params": {}}] * 65},
    ],
)
def test_corrupt_rules(store, payload):
    store._dir.mkdir()
    (store._dir / "rules.json").write_text(json.dumps(payload))
    with pytest.raises(StoreCorrupt):
        store.load_rules()


def test_too_many_rules_refused(store):
    from fwmanager.core.rulebook import MAX_RULES, Rule

    many = [Rule(f"r{i}", "hide", {"profiles": ["casa"]}) for i in range(MAX_RULES + 1)]
    with pytest.raises(ValidationError):
        store.save_rules(many)
