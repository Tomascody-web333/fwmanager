import pytest

from fwmanager.core import validate as v
from fwmanager.errors import ValidationError

EVIL = ['a"b', "a\nb", "a b", "a;b", "a{b", "", "A", "1abc", "a" * 33, "a\x00b", "../x", None, 5]


@pytest.mark.parametrize("bad", EVIL)
def test_name_rejects(bad):
    with pytest.raises(ValidationError):
        v.validate_name(bad)


@pytest.mark.parametrize("ok", ["casa", "a", "my-profile_2"])
def test_name_accepts(ok):
    assert v.validate_name(ok) == ok


@pytest.mark.parametrize("bad", [0, -1, 65536, "22", 22.0, True, None])
def test_port_rejects(bad):
    with pytest.raises(ValidationError):
        v.validate_port(bad)


@pytest.mark.parametrize("ok", [1, 22, 65535])
def test_port_accepts(ok):
    assert v.validate_port(ok) == ok


@pytest.mark.parametrize(
    "bad",
    ["22", "22/icmp", "0/tcp", "70000/tcp", "10-5/tcp", "22/tcp; drop", "22/tcp\n", "-1/tcp", 22],
)
def test_port_spec_rejects(bad):
    with pytest.raises(ValidationError):
        v.parse_port_spec(bad)


def test_port_spec_parses():
    assert v.parse_port_spec("22/tcp") == (22, 22, "tcp")
    assert v.parse_port_spec("1714-1764/udp") == (1714, 1764, "udp")


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "1.2.3.4/33",
        "1.2.3.4/24",
        "::1/129",
        "a.b.c.d",
        "10.0.0.0/8 ",
        "10.0.0.0/8\n",
        "10.0.0.0/8, 0.0.0.0/0",
        "fe80::1%eth0",
        "1.2.3.4;drop",
        None,
        5,
    ],
)
def test_cidr_rejects(bad):
    with pytest.raises(ValidationError):
        v.validate_cidr(bad)


def test_cidr_canonical():
    assert v.validate_cidr("192.168.1.0/24") == "192.168.1.0/24"
    assert v.validate_cidr("10.1.2.3") == "10.1.2.3/32"
    assert v.validate_cidr("FE80::/10") == "fe80::/10"


def test_cidr_list_limits():
    with pytest.raises(ValidationError):
        v.validate_cidrs([])
    with pytest.raises(ValidationError):
        v.validate_cidrs("10.0.0.0/8")
    with pytest.raises(ValidationError):
        v.validate_cidrs([f"10.0.{i % 250}.0/24" for i in range(65)] + ["1.1.1.1"])
    assert v.validate_cidrs(["10.0.0.0/8", "10.0.0.0/8"]) == ("10.0.0.0/8",)
