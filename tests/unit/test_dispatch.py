import subprocess

import pytest

from fwmanager.cli import dispatch as d
from fwmanager.errors import FwError

UUID = "123e4567-e89b-12d3-a456-426614174000"
GW4 = d.Gateway("192.168.1.1", 4)
GW6 = d.Gateway("fe80::1", 6)


# -- environment parsing ---


def test_parse_context_valid():
    ctx = d.parse_context(
        "wlan0",
        {"CONNECTION_UUID": UUID, "IP4_GATEWAY": "192.168.1.1", "IP6_GATEWAY": "fe80::1%wlan0"},
    )
    assert ctx.uuid == UUID and ctx.gateways == (GW4, GW6)


@pytest.mark.parametrize("iface", ["", "a b", "wlan0;reboot", "x" * 16, "a\nb", "$(id)"])
def test_bad_interface_rejected(iface):
    with pytest.raises(FwError):
        d.parse_context(iface, {})


def test_bad_optional_values_are_dropped():
    env = {
        "CONNECTION_UUID": "nonsense",
        "IP4_GATEWAY": "1.2.3.4; reboot",
        "IP6_GATEWAY": "192.168.1.1",
    }  # an IPv4 address in the IPv6 slot
    ctx = d.parse_context("eth0", env)
    assert ctx.uuid == "" and ctx.gateways == ()


@pytest.mark.parametrize("gw", ["0.0.0.0", "224.0.0.1", "", "  "])
def test_unusable_gateways_dropped(gw):
    assert d.parse_context("eth0", {"IP4_GATEWAY": gw}).gateways == ()


# -- nmcli ---


def fake_run(stdout="", rc=0, exc=None, field="802-11-wireless.ssid"):
    def run(argv, **kw):
        assert argv[-2:] == ["uuid", UUID] and field in argv
        if exc:
            raise exc
        return subprocess.CompletedProcess(argv, rc, stdout, "")

    return run


@pytest.fixture(autouse=False)
def with_nmcli(monkeypatch):
    monkeypatch.setattr(d.os, "access", lambda p, m: True)


def test_lookup_ssid(with_nmcli):
    assert d.lookup_ssid(UUID, fake_run("Casa WiFi\n")) == "Casa WiFi"
    assert d.lookup_ssid(UUID, fake_run("A\\:B\\\\C\n")) == "A:B\\C"
    assert d.lookup_ssid(UUID, fake_run("", rc=10)) == ""
    assert d.lookup_ssid(UUID, fake_run("", exc=OSError())) == ""
    assert d.lookup_ssid(UUID, fake_run("", exc=subprocess.TimeoutExpired("x", 5))) == ""
    assert d.lookup_ssid(UUID, fake_run("x" * 40)) == ""  # not a valid SSID
    assert d.lookup_ssid("", fake_run("Casa")) == ""


def test_lookup_kind(with_nmcli):
    def kind(out, **kw):
        return d.lookup_kind(UUID, fake_run(out, field="connection.type", **kw))

    assert kind("802-3-ethernet\n") == "802-3-ethernet"
    assert kind("802-11-wireless\n") == "802-11-wireless"
    assert kind("", rc=10) == "" and kind("", exc=OSError()) == ""
    assert kind("eth; reboot\n") == "" and kind("x" * 40) == ""


# -- ICMP packet ---


def test_checksum_and_packet():
    pkt = d.build_echo_v4(0x1234, 7)
    assert pkt[0] == 8 and d.checksum(pkt) == 0  # a valid checksum sums to zero
    assert d.checksum(b"\x00\x01\xf2\x03\xf4\xf5\xf6\xf7") == 0x220D  # RFC 1071 example
    assert d.checksum(b"\x01") == 0xFEFF  # odd length


# -- decision flow ---


class FakeClient:
    def __init__(self, mapping=None, active="", pending=(), conflicts=(), apply_exc=None):
        self.mapping = mapping or {}
        self.active = active
        self.pending = list(pending)  # successive pending_profile values
        self.conflicts = list(conflicts)
        self.apply_exc = apply_exc
        self.calls = []

    def get_network_profile(self, ssid, uuid):
        return self.mapping.get(uuid or ssid, "")

    def status(self):
        pending = (
            self.pending.pop(0)
            if len(self.pending) > 1
            else (self.pending[0] if self.pending else "")
        )
        return {
            "active_profile": self.active,
            "pending_profile": pending,
            "blocking_conflicts": self.conflicts,
        }

    def apply_profile(self, name):
        self.calls.append(("apply", name))
        if self.apply_exc:
            raise self.apply_exc

    def confirm(self):
        self.calls.append(("confirm",))

    def rollback(self):
        self.calls.append(("rollback",))


def ctx(*gws):
    return d.Context("wlan0", UUID, tuple(gws))


def pinger(before=True, after=True):
    state = {"applied": False, "log": []}

    def ping(addr, iface, timeout):
        state["log"].append(addr)
        return after if state["applied"] else before

    return ping, state


def test_mapped_profile_applied_and_confirmed():
    client = FakeClient({UUID: "home"})
    ping, state = pinger()
    # the fake pinger flips to "after" mode when apply is called
    orig = client.apply_profile
    client.apply_profile = lambda n: (state.update(applied=True), orig(n))
    assert d.dispatch(client, ctx(GW4), ping=ping, sleep=lambda s: None) == d.EXIT_OK
    assert client.calls == [("apply", "home"), ("confirm",)]


def test_unknown_network_gets_publico():
    client = FakeClient()
    ping, _ = pinger()
    assert d.dispatch(client, ctx(GW4), ping=ping, sleep=lambda s: None) == d.EXIT_OK
    assert client.calls[0] == ("apply", "public")


@pytest.mark.parametrize("kind", ["802-3-ethernet", "bond", "vlan"])
def test_an_unknown_wired_connection_keeps_the_current_profile(kind):
    client = FakeClient(active="home")
    ping, state = pinger()
    c = d.Context("eth0", UUID, (GW4,))
    assert d.dispatch(client, c, ping=ping, sleep=lambda s: None, kind=kind) == d.EXIT_OK
    assert client.calls == [] and state["log"] == []


def test_a_wired_connection_with_a_profile_still_gets_it():
    client = FakeClient({UUID: "work"}, active="home")
    ping, state = pinger()
    orig = client.apply_profile
    client.apply_profile = lambda n: (state.update(applied=True), orig(n))
    c = d.Context("eth0", UUID, (GW4,))
    assert d.dispatch(client, c, ping=ping, sleep=lambda s: None, kind="802-3-ethernet") == 0
    assert client.calls == [("apply", "work"), ("confirm",)]


@pytest.mark.parametrize("kind", ["802-11-wireless", "gsm", ""])
def test_unknown_wifi_mobile_or_unidentified_still_get_publico(kind):
    client = FakeClient(active="home")
    ping, _ = pinger()
    assert d.dispatch(client, ctx(GW4), ping=ping, sleep=lambda s: None, kind=kind) == 0
    assert client.calls[0] == ("apply", "public")


def test_already_active_is_a_noop():
    client = FakeClient({UUID: "home"}, active="home")
    ping, state = pinger()
    assert d.dispatch(client, ctx(GW4), ping=ping, sleep=lambda s: None) == d.EXIT_OK
    assert client.calls == [] and state["log"] == []


def test_connectivity_lost_triggers_rollback():
    client = FakeClient({UUID: "home"})
    ping, state = pinger(before=True, after=False)
    orig = client.apply_profile
    client.apply_profile = lambda n: (state.update(applied=True), orig(n))
    assert d.dispatch(client, ctx(GW4), ping=ping, sleep=lambda s: None) == d.EXIT_REVERTED
    assert client.calls == [("apply", "home"), ("rollback",)]
    assert state["log"].count("192.168.1.1") == 1 + d.PING_TRIES  # baseline + retries


def test_one_of_two_gateways_lost_triggers_rollback():
    client = FakeClient({UUID: "home"})
    state = {"applied": False}

    def ping(addr, iface, timeout):
        return True if not state["applied"] else addr == GW4.address

    orig = client.apply_profile
    client.apply_profile = lambda n: (state.update(applied=True), orig(n))
    assert d.dispatch(client, ctx(GW4, GW6), ping=ping, sleep=lambda s: None) == d.EXIT_REVERTED


def test_silent_gateway_cannot_judge_so_confirms():
    client = FakeClient({UUID: "home"})
    ping, _ = pinger(before=False, after=False)
    assert d.dispatch(client, ctx(GW4), ping=ping, sleep=lambda s: None) == d.EXIT_OK
    assert client.calls == [("apply", "home"), ("confirm",)]


def test_no_gateway_confirms():
    client = FakeClient({UUID: "home"})
    ping, state = pinger()
    assert d.dispatch(client, ctx(), ping=ping, sleep=lambda s: None) == d.EXIT_OK
    assert client.calls == [("apply", "home"), ("confirm",)] and state["log"] == []


def test_transient_ping_loss_is_retried():
    client = FakeClient({UUID: "home"})
    answers = iter([True, False, False, True])  # baseline ok, then two losses, then ok

    def ping(addr, iface, timeout):
        return next(answers)

    assert d.dispatch(client, ctx(GW4), ping=ping, sleep=lambda s: None) == d.EXIT_OK
    assert client.calls[-1] == ("confirm",)


def test_other_firewall_active_leaves_everything():
    client = FakeClient({UUID: "home"}, conflicts=["ufw"])
    ping, _ = pinger()
    assert d.dispatch(client, ctx(GW4), ping=ping, sleep=lambda s: None) == d.EXIT_OK
    assert client.calls == []


def test_waits_for_a_pending_change_then_proceeds():
    client = FakeClient({UUID: "home"}, pending=["work", "work", ""])
    sleeps = []
    ping, _ = pinger()
    code = d.dispatch(client, ctx(), ping=ping, sleep=sleeps.append)
    assert code == d.EXIT_OK and len(sleeps) >= 1
    assert client.calls[0] == ("apply", "home")


def test_gives_up_if_change_stays_pending():
    client = FakeClient({UUID: "home"}, pending=["work"])
    ping, _ = pinger()
    assert d.dispatch(client, ctx(), ping=ping, sleep=lambda s: None) == d.EXIT_ERROR
    assert client.calls == []


def test_daemon_errors_do_not_confirm(monkeypatch):
    client = FakeClient({UUID: "home"}, apply_exc=FwError("x", code="apply_pending"))
    monkeypatch.setattr(d, "Client", lambda addr: client)
    monkeypatch.setattr(d, "lookup_ssid", lambda u: "")
    monkeypatch.setattr(d, "lookup_kind", lambda u: "")
    assert d.run("wlan0", env={"CONNECTION_UUID": UUID}) == d.EXIT_ERROR
    assert ("confirm",) not in client.calls


def test_unidentifiable_network_falls_back_without_asking_the_daemon():
    client = FakeClient({"": "home"})  # would wrongly match if it were consulted
    client.get_network_profile = lambda s, u: pytest.fail("must not be called")
    ping, _ = pinger()
    c = d.Context("eth0", "", ())
    assert d.dispatch(client, c, ping=ping, sleep=lambda s: None) == d.EXIT_OK
    assert client.calls[0] == ("apply", "public")


def test_run_asks_networkmanager_for_the_connection_type(monkeypatch):
    client = FakeClient(active="home")
    monkeypatch.setattr(d, "Client", lambda addr: client)
    monkeypatch.setattr(d, "lookup_ssid", lambda u: "")
    monkeypatch.setattr(d, "lookup_kind", lambda u: "802-3-ethernet")
    assert d.run("eth0", env={"CONNECTION_UUID": UUID}) == d.EXIT_OK
    assert client.calls == []
