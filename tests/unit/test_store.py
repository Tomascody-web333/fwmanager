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
    store.save_state("home")
    assert store.load_state() == "home"
    store.save_state("public")
    assert store.load_state() == "public"
    assert not list(store._dir.glob(".*.tmp"))  # no temp leftovers


@pytest.mark.parametrize(
    "content",
    [
        "",
        "{",
        "[]",
        "null",
        '{"version": 2, "profile": "home"}',
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
        Store(f / "sub").save_state("home")  # parent is a file


def test_state_files_are_readable_by_root_only(store):
    (store._dir).mkdir()
    (store._dir / ".networks.json.tmp").write_text("left over")
    os.chmod(store._dir / ".networks.json.tmp", 0o644)
    store.save_state("home")
    store.save_networks([Network("Casa WiFi", UUID, "home")])
    for name in ("state.json", "networks.json"):
        assert (os.stat(store._dir / name).st_mode & 0o777) == 0o600, name


def test_a_new_state_directory_is_private(tmp_path):
    Store(tmp_path / "new").save_state("home")
    assert (os.stat(tmp_path / "new").st_mode & 0o777) == 0o700


def test_networks_roundtrip(store):
    nets = [Network("Casa WiFi", UUID, "home"), Network("Café ☕", "", "public")]
    store.save_networks(nets)
    assert store.load_networks() == nets
    assert Store(store._dir).load_networks() == nets


def test_networks_missing_is_empty(store):
    assert store.load_networks() == []


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(ssid="", uuid="", profile="home"),
        dict(ssid="a\nb", uuid="", profile="home"),
        dict(ssid="x" * 33, uuid="", profile="home"),
        dict(ssid="ç" * 17, uuid="", profile="home"),  # 34 bytes
        dict(ssid="ok", uuid="not-a-uuid", profile="home"),
        dict(ssid="ok", uuid=UUID.upper(), profile="home"),
        dict(ssid="ok", uuid=UUID.replace("-", ""), profile="home"),
        dict(ssid="ok", uuid="", profile='x"y'),
        dict(ssid="ok", uuid="", profile="Casa"),
    ],
)
def test_network_validation(kwargs):
    with pytest.raises(ValidationError):
        Network(**kwargs)


def test_networks_limit(store):
    many = [Network(f"n{i}", "", "home") for i in range(MAX_NETWORKS + 1)]
    with pytest.raises(ValidationError):
        store.save_networks(many)


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 1, "networks": "x"},
        {"version": 1, "networks": [{"ssid": "a", "profile": "BAD NAME"}]},
        {"version": 1, "networks": [{"profile": "home"}]},
        {"version": 1, "networks": ["str"]},
        {"version": 1, "networks": [{"ssid": "a", "uuid": "zzz", "profile": "home"}]},
    ],
)
def test_corrupt_networks(store, payload):
    store._dir.mkdir()
    (store._dir / "networks.json").write_text(json.dumps(payload))
    with pytest.raises(StoreCorrupt):
        store.load_networks()


def test_find_profile_prefers_uuid():
    nets = [Network("Cafe", "", "public"), Network("Cafe", UUID, "home")]
    assert find_profile(nets, "Cafe", UUID) == "home"
    assert find_profile(nets, "Cafe", "") == "public"
    assert find_profile(nets, "Other", "") is None
    assert find_profile(nets, "", "") is None


def test_same_ssid_different_uuid_does_not_match():
    nets = [Network("Cafe", UUID, "home")]
    other = "223e4567-e89b-12d3-a456-426614174000"
    assert find_profile(nets, "Cafe", other) is None  # a different connection


# -- edited profiles and options ---


def test_profiles_roundtrip(store):
    from fwmanager.core.profiles import Profile, Source

    custom = {
        "home": Profile("home", ("ssh", "web"), True, False, Source("cidrs", ("10.1.0.0/16",))),
        "public": Profile("public", (), False, False, Source("any")),
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
        {"version": 1, "profiles": {"home": {}}},
        {"version": 1, "profiles": {"CASA": {"services": [], "ping": 1, "mdns": 0,
                                              "source": {"kind": "any"}}}},
        {"version": 1, "profiles": {"home": {"services": ["a b"], "ping": 1, "mdns": 0,
                                              "source": {"kind": "any"}}}},
        {"version": 1, "profiles": {"home": {"services": [], "ping": 1, "mdns": 0,
                                              "source": {"kind": "bogus"}}}},
        {"version": 1, "profiles": {"home": {"services": [], "ping": 1, "mdns": 0,
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
        Rule("r1", "allow_only_in", {"service": "web", "profiles": ("home", "work")}),
        Rule("r2", "hide", {"profiles": ("public",)}),
    ]
    store.save_rules(rules)
    loaded = store.load_rules()
    assert [r.id for r in loaded] == ["r1", "r2"]
    assert loaded[0].params == {"service": "web", "profiles": ["home", "work"]}
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

    many = [Rule(f"r{i}", "hide", {"profiles": ["home"]}) for i in range(MAX_RULES + 1)]
    with pytest.raises(ValidationError):
        store.save_rules(many)


def test_old_portuguese_profile_ids_are_read_as_english(store):
    store._dir.mkdir(parents=True, exist_ok=True)
    old = {  # files exactly as an install from before the rename left them
        "state.json": {"profile": "casa"},
        "networks.json": {"networks": [
            {"ssid": "Cafe", "uuid": "", "profile": "publico"},
            {"ssid": "Office", "uuid": "", "profile": "trabalho"},
            {"ssid": "Lab", "uuid": "", "profile": "lab"},
        ]},
        "profiles.json": {"profiles": {"personalizado": {
            "services": ["ssh"], "ping": True, "mdns": False, "source": {"kind": "any"}}}},
        "rules.json": {"rules": [
            {"id": "r1", "template": "hide", "params": {"profiles": ["casa", "lab"]}}]},
    }  # fmt: skip
    for name, data in old.items():
        store._write(name, data)
    assert store.load_state() == "home"
    assert [n.profile for n in store.load_networks()] == ["public", "work", "lab"]
    custom = store.load_profiles()
    assert list(custom) == ["custom"] and custom["custom"].name == "custom"
    assert store.load_rules()[0].params["profiles"] == ["home", "lab"]
