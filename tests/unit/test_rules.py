import pytest

from fwmanager.core.profiles import DEFAULT_PROFILES, Profile, Source
from fwmanager.core.rules import RenderOptions, render
from fwmanager.core.services import load_builtin
from fwmanager.errors import ValidationError

CAT = load_builtin()


def r(name_or_profile, **opts):
    p = DEFAULT_PROFILES[name_or_profile] if isinstance(name_or_profile, str) else name_or_profile
    return render(p, CAT, RenderOptions(**opts))


def test_atomic_header():
    lines = r("publico").splitlines()
    assert lines[:3] == [
        "add table inet fwmanager",
        "delete table inet fwmanager",
        "table inet fwmanager {",
    ]


def test_input_policy_drop_and_mandatory_exceptions():
    out = r("publico")
    assert "policy drop" in out
    for needle in (
        "ct state established,related",
        "ct state invalid",
        'iif "lo" accept',
        "nd-neighbor-solicit",
        "nd-router-advert",
        "ip6 hoplimit 255",
        "packet-too-big",
        "udp sport 67 udp dport 68",
        "udp sport 547 udp dport 546",
    ):
        assert needle in out


def test_publico_is_most_restrictive():
    out = r("publico")
    assert "echo-request" not in out
    assert "5353" not in out
    assert "dport" not in out.replace("udp dport 68", "").replace("udp dport 546", "")


def test_no_global_flush():
    assert "flush ruleset" not in r("casa")


def test_ping_flag():
    assert "icmp type echo-request" in r("casa")
    assert "icmpv6 type echo-request" in r("casa")
    p = Profile("x", ping=False)
    assert "echo-request" not in r(p)


def test_mdns_flag():
    assert "udp dport 5353" in r(Profile("x", mdns=True))
    assert "5353" not in r(Profile("x", mdns=False))


def test_services_lan_source():
    out = r("casa")
    assert "ip saddr { 10.0.0.0/8" in out
    assert "tcp dport { 22, 139, 445, 1714-1764, 22000, 631 }" in out


def test_services_any_source():
    out = r(Profile("x", services=("ssh",)))
    assert "\n\t\ttcp dport { 22 } counter accept" in out


def test_services_cidrs_split_by_family():
    p = Profile("x", services=("ssh",), source=Source("cidrs", ("192.168.1.0/24", "2001:db8::/32")))
    out = r(p)
    assert "ip saddr { 192.168.1.0/24 } tcp dport { 22 }" in out
    assert "ip6 saddr { 2001:db8::/32 } tcp dport { 22 }" in out


def test_unknown_service_rejected():
    with pytest.raises(ValidationError):
        r(Profile("x", services=("nope",)))


def test_forward_default_accept_and_router_mode():
    assert "hook forward priority filter; policy accept" in r("casa")
    out = r("casa", router_mode=True)
    assert "hook forward priority filter; policy drop" in out


def test_output_accept():
    assert "hook output priority filter; policy accept" in r("casa")


def test_log_optional_and_rate_limited():
    assert "log prefix" not in r("casa")
    out = r("casa", log_blocked=True, log_rate="5/minute")
    assert 'limit rate 5/minute log prefix "fwmanager-blocked: "' in out


@pytest.mark.parametrize(
    "bad", ['5/minute"; drop', "0/second", "x/second", "5/year", "5", "5/minute\n"]
)
def test_bad_log_rate(bad):
    with pytest.raises(ValidationError):
        RenderOptions(log_blocked=True, log_rate=bad)


def test_default_profiles_are_valid_and_render():
    assert set(DEFAULT_PROFILES) == {"casa", "trabalho", "publico", "personalizado"}
    for p in DEFAULT_PROFILES.values():
        assert f'comment "profile:{p.name}"' in r(p)


@pytest.mark.parametrize("bad", ['a"b', "a\nb", "a b", "x; flush ruleset", ""])
def test_profile_name_injection_rejected(bad):
    with pytest.raises(ValidationError):
        Profile(bad)


def test_source_invariants():
    with pytest.raises(ValidationError):
        Source("bogus")
    with pytest.raises(ValidationError):
        Source("cidrs", ())
    with pytest.raises(ValidationError):
        Source("lan", ("10.0.0.0/8",))
    with pytest.raises(ValidationError):
        Source("cidrs", ("0.0.0.0/0\naccept",))
