import configparser
import grp
import pwd
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from fwmanager.daemon import auth
from fwmanager.daemon.interface import interface_info
from fwmanager.errors import code_from_dbus_error, dbus_error_name

DATA = Path(__file__).resolve().parents[2] / "data"


def test_error_name_roundtrip():
    for code in ("invalid_profile", "apply_pending", "no_pending", "access_denied", "nft_failed"):
        name = dbus_error_name(code)
        assert name.startswith("org.fwmanager.Error.") and "_" not in name
        assert code_from_dbus_error(name) == code
    assert code_from_dbus_error("org.freedesktop.DBus.Error.NoReply") == "internal_error"


def test_is_member(monkeypatch):
    fake_group = grp.struct_group(("fwmanager", "x", 2000, ["alice"]))

    def fake_pw(uid):
        users = {
            1000: pwd.struct_passwd(("alice", "x", 1000, 1000, "", "/home/alice", "/bin/sh")),
            1001: pwd.struct_passwd(("bob", "x", 1001, 1001, "", "/home/bob", "/bin/sh")),
            1002: pwd.struct_passwd(("carol", "x", 1002, 2000, "", "/home/carol", "/bin/sh")),
        }
        if uid not in users:
            raise KeyError(uid)
        return users[uid]

    monkeypatch.setattr(auth.grp, "getgrnam", lambda n: fake_group)
    monkeypatch.setattr(auth.pwd, "getpwuid", fake_pw)
    assert auth.is_member(1000)  # listed member
    assert not auth.is_member(1001)
    assert auth.is_member(1002)  # primary group
    assert not auth.is_member(4242)  # unknown uid
    assert not auth.is_member(0)  # root gets no implicit pass


def test_is_member_missing_group(monkeypatch):
    def boom(_n):
        raise KeyError

    monkeypatch.setattr(auth.grp, "getgrnam", boom)
    assert not auth.is_member(0)


def test_interface_is_fixed_command_set():
    info = interface_info()
    assert {m.name for m in info.methods} == {
        "ApplyProfile",
        "Confirm",
        "Rollback",
        "GetStatus",
        "ListProfiles",
        "CheckProfile",
        "GetProfile",
        "SetProfile",
        "ResetProfile",
        "ListServices",
        "GetOptions",
        "SetOptions",
        "GetEvents",
        "GetRecentBlocked",
        "ListNetworks",
        "SetNetworkProfile",
        "RemoveNetwork",
        "GetNetworkProfile",
    }
    apply = next(m for m in info.methods if m.name == "ApplyProfile")
    assert [a.signature for a in apply.in_args] == ["s"]


def test_dbus_policy_shape():
    root = ET.parse(DATA / "dbus" / "org.fwmanager.Control.conf").getroot()
    pols = {(p.get("user"), p.get("group"), p.get("context")): p for p in root.findall("policy")}
    owner = pols[("root", None, None)]
    assert owner.find("allow").get("own") == "org.fwmanager.Control"
    group = pols[(None, "fwmanager", None)]
    assert any(a.get("send_destination") == "org.fwmanager.Control" for a in group.findall("allow"))
    default = pols[(None, None, "default")]
    assert all(e.tag == "deny" for e in default)
    assert any(e.get("send_destination") for e in default)
    assert len(pols) == 3  # nobody else is allowed anything


@pytest.fixture
def unit():
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    cp.read(DATA / "systemd" / "fwmanagerd.service")
    return cp["Service"]


def test_systemd_unit_hardening(unit):
    expected = {
        "User": "root",
        "CapabilityBoundingSet": "CAP_NET_ADMIN CAP_NET_RAW",
        "AmbientCapabilities": "CAP_NET_ADMIN CAP_NET_RAW",
        "NoNewPrivileges": "yes",
        "ProtectSystem": "strict",
        "ProtectHome": "yes",
        "PrivateTmp": "yes",
        "RestrictAddressFamilies": "AF_UNIX AF_NETLINK AF_INET AF_INET6",
        "ReadWritePaths": "/etc/ufw /etc/default /run",
        "StateDirectory": "fwmanager",
    }
    for key, value in expected.items():
        assert unit[key] == value, key


def test_remote_error_prefix_is_stripped():
    from fwmanager.client import _strip_remote_prefix

    name = "org.fwmanager.Error.NoPending"
    assert _strip_remote_prefix(f"GDBus.Error:{name}: nothing to confirm", name) == (
        "nothing to confirm"
    )
    assert _strip_remote_prefix("plain text", name) == "plain text"
    assert _strip_remote_prefix("GDBus.Error:x: y", None) == "GDBus.Error:x: y"
    # a colon inside the daemon's own message must survive
    assert _strip_remote_prefix(f"GDBus.Error:{name}: a: b", name) == "a: b"
