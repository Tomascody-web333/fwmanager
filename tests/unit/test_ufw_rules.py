import pytest

from fwmanager.core import ufw_rules as ur
from fwmanager.core.profiles import DEFAULT_PROFILES, Profile, Source
from fwmanager.core.services import load_builtin
from fwmanager.errors import ValidationError

CAT = load_builtin()


def argvs(profile, ipv6=True):
    return [r.add_argv(profile.name) for r in ur.translate(profile, CAT, ipv6)]


def test_publico_has_no_rules():
    assert ur.translate(DEFAULT_PROFILES["public"], CAT) == []


def test_casa_rules_lan_v4_and_v6():
    rules = ur.translate(DEFAULT_PROFILES["home"], CAT)
    sources = {r.source for r in rules}
    assert sources == set(ur.LAN_V4) | set(ur.LAN_V6)
    tcp = next(r for r in rules if r.proto == "tcp" and r.source == "192.168.0.0/16")
    assert tcp.ports == ("22", "139", "445", "631", "1714:1764", "22000")  # sorted like ufw
    assert tcp.add_argv("home") == [
        "allow", "from", "192.168.0.0/16", "to", "any", "port",
        "22,139,445,631,1714:1764,22000", "proto", "tcp", "comment", "fwmanager:home",
    ]  # fmt: skip


def test_ipv6_sources_dropped_when_ipv6_is_off():
    rules = ur.translate(DEFAULT_PROFILES["home"], CAT, ipv6=False)
    assert rules and not any(r.is_v6 for r in rules)


def test_any_source_and_cidr_source():
    r = ur.translate(Profile("x", services=("ssh",)), CAT)
    assert [x.source for x in r] == ["any"] and r[0].delete_argv() == [
        "delete", "allow", "from", "any", "to", "any", "port", "22", "proto", "tcp"]  # fmt: skip
    p = Profile("x", services=("ssh",), source=Source("cidrs", ("203.0.113.0/24", "2001:db8::/32")))
    assert {x.source for x in ur.translate(p, CAT)} == {"203.0.113.0/24", "2001:db8::/32"}
    assert {x.source for x in ur.translate(p, CAT, ipv6=False)} == {"203.0.113.0/24"}


def test_multiport_chunking_respects_the_limit():
    ports = [str(n) for n in range(1000, 1040)]
    chunks = ur._chunks(ports)
    assert all(sum(2 if ":" in p else 1 for p in c) <= 15 for c in chunks)
    assert [p for c in chunks for p in c] == ports
    ranges = [f"{n}:{n + 1}" for n in range(2000, 2020, 2)]
    assert all(sum(2 for _ in c) <= 15 for c in ur._chunks(ranges))


def test_unknown_service_rejected():
    with pytest.raises(ValidationError):
        ur.translate(Profile("x", services=("nope",)), CAT)


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(proto="icmp", source="any", ports=("22",)),
        dict(proto="tcp", source="1.2.3.4; reboot", ports=("22",)),
        dict(proto="tcp", source="any", ports=()),
        dict(proto="tcp", source="any", ports=("22; drop",)),
        dict(proto="tcp", source="any", ports=("22", "a")),
        dict(proto="tcp", source="any", ports=tuple(str(n) for n in range(16))),
        dict(proto="tcp", source="10.0.0.1/8", ports=("22",)),
    ],
)
def test_rule_validation(kwargs):
    with pytest.raises(ValidationError):
        ur.Rule(**kwargs)


def test_profile_name_in_comment_is_validated():
    r = ur.Rule("tcp", "any", ("22",))
    with pytest.raises(ValidationError):
        r.add_argv('x"; ufw disable')


# -- parsing `ufw show added` ---

LISTING = """Added user rules (see 'ufw status' for running firewall):
ufw allow from 192.168.0.0/16 to any port 22,139 proto tcp comment 'fwmanager:home'
ufw allow from 10.0.0.0/8 to any port 1714:1764 proto udp comment 'fwmanager:home'
ufw allow from any to any port 8080 proto tcp
ufw allow from any to any port 22 proto tcp comment 'my own rule'
ufw deny from 1.2.3.4
ufw allow 53
ufw limit 22/tcp comment 'fwmanager:home'
ufw allow 443/tcp
ufw allow 1714:1764,22000/udp comment 'fwmanager:personal'
"""


def test_parse_added_separates_ours_from_the_users():
    lst = ur.parse_added(LISTING)
    assert lst.managed == {
        ur.Rule("tcp", "192.168.0.0/16", ("22", "139")): "home",
        ur.Rule("udp", "10.0.0.0/8", ("1714:1764",)): "home",
        ur.Rule("udp", "any", ("1714:1764", "22000")): "personal",
    }
    assert ur.Rule("tcp", "any", ("8080",)) in lst.foreign
    assert ur.Rule("tcp", "any", ("22",)) in lst.foreign  # user's comment is not ours
    assert lst.unparsed_managed == 1  # `ufw limit ...` carries our comment but is not our shape
    assert ur.Rule("tcp", "any", ("443",)) in lst.foreign  # short form "443/tcp"


@pytest.mark.parametrize(
    "line",
    [
        "ufw allow from 1.2.3.4 to any port 22 proto tcp comment 'unterminated",
        "ufw allow from 1.2.3.4/24 to any port 22 proto tcp",  # host bits set
        "ufw allow from 1.2.3.4 to 5.6.7.8 port 22 proto tcp",
        "ufw allow from 1.2.3.4 to any port 22;reboot proto tcp",
        "ufw allow from any to any port 22 proto icmp",
        "rm -rf /",
        "",
    ],
)
def test_parse_rejects_odd_lines(line):
    assert ur.parse_rule_line(line) is None


def test_port_order_is_canonical():
    a = ur.Rule("tcp", "any", ("1714:1764", "22", "631"))
    assert a.ports == ("22", "631", "1714:1764")
    assert a == ur.Rule("tcp", "any", ("631", "22", "1714:1764"))


@pytest.mark.parametrize(
    "line", ["ufw allow 99999999/tcp", "ufw allow 22/icmp", "ufw allow 22,/tcp"]
)
def test_short_form_garbage_rejected(line):
    assert ur.parse_rule_line(line) is None


def test_a_profile_that_would_be_slow_to_apply_is_refused():
    catalog = load_builtin()
    biggest_default = Profile("home", tuple(catalog), source=Source("lan"))
    ur.check_size(biggest_default, catalog)  # every service, local network: fine
    many = tuple(f"10.{i}.0.0/16" for i in range(64))
    with pytest.raises(ValidationError) as exc:
        ur.check_size(Profile("home", ("ssh",), source=Source("cidrs", many)), catalog)
    assert exc.value.code == "profile_too_big"
    at_limit = Profile("home", ("ssh",), source=Source("cidrs", many[: ur.MAX_RULES_PER_PROFILE]))
    ur.check_size(at_limit, catalog)


def test_ports_must_be_ascii_digits():
    with pytest.raises(ValidationError):
        ur.Rule("tcp", "any", ("٢٢",))
