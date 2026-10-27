import socket
import struct
import threading

import pytest

from fwmanager import policytest as pt
from fwmanager.errors import FwError


def test_default_gateway_v4(tmp_path):
    f = tmp_path / "route"
    f.write_text(
        "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
        "eth0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\n"
        "eth0\t0001A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\n"
    )
    assert pt.default_gateways_v4(f) == [("eth0", "192.168.1.1")]
    assert pt.default_gateways_v4(tmp_path / "missing") == []


def test_default_gateway_v6(tmp_path):
    f = tmp_path / "ipv6_route"
    zero = "0" * 32
    gw = "fe800000000000000000000000000001"
    f.write_text(
        f"{zero} 00 {zero} 00 {gw} 00000064 00000001 00000000 00000003 wlan0\n"
        f"{zero} 00 {zero} 00 {zero} 00000064 00000001 00000000 00000003 lo\n"
    )
    assert pt.default_gateways_v6(f) == [("wlan0", "fe80::1")]


def test_resolvers_prefer_real_upstream(tmp_path):
    f = tmp_path / "resolv.conf"
    f.write_text(
        "# x\nnameserver 127.0.0.53\nnameserver 9.9.9.9\nnameserver bogus\n"
        "nameserver fe80::1%eth0\nsearch lan\n"
    )
    assert pt.resolvers(f) == ["9.9.9.9", "127.0.0.53"]
    assert pt.resolvers(tmp_path / "missing") == []


def test_dns_packet_shape():
    ident, pkt = pt.dns_query_packet()
    assert struct.unpack("!H", pkt[:2])[0] == ident and pkt[4:6] == b"\x00\x01"


def serve_dns_once(reply_ok=True):
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))
    srv.settimeout(5)

    def run():
        try:
            data, addr = srv.recvfrom(512)
            if reply_ok:
                srv.sendto(data[:2] + b"\x81\x80" + data[4:], addr)
        except OSError:
            pass
        finally:
            srv.close()

    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1]


def test_dns_probe_ok_and_fail(monkeypatch):
    port = serve_dns_once()
    assert pt.probe_dns(["127.0.0.1"], port=port).ok is True
    monkeypatch.setattr(pt, "DNS_TIMEOUT", 0.2)
    port = serve_dns_once(reply_ok=False)
    assert pt.probe_dns(["127.0.0.1"], port=port).ok is False
    assert pt.probe_dns([]).ok is None


def test_gateway_probe_uses_ping_with_retries():
    calls = []

    def ping(addr, iface, timeout):
        calls.append(addr)
        return len(calls) == 2  # first try lost, second answered

    p = pt.probe_gateways(4, [("eth0", "192.168.1.1")], ping)
    assert p.ok is True and calls == ["192.168.1.1"] * 2
    assert pt.probe_gateways(6, [], ping).ok is None
    assert pt.probe_gateways(4, [("eth0", "1.1.1.1")], lambda *a: False).ok is False


def test_loopback_probe_unavailable_is_not_applicable(monkeypatch):
    def boom(*a, **k):
        raise OSError(97, "Address family not supported")

    monkeypatch.setattr(pt.socket, "socket", boom)
    assert pt.probe_loopback(socket.AF_INET6, "::1", "loopback_v6").ok is None


# -- live test orchestration ---


class FakeClient:
    def __init__(self, active="casa", pending="", conflicts=(), restore=True, apply_exc=None):
        self.active, self.pending, self.conflicts = active, pending, list(conflicts)
        self.restore, self.apply_exc = restore, apply_exc
        self.calls = []
        self.current = active

    def status(self):
        return {
            "active_profile": self.current,
            "firewall_active": True,
            "pending_profile": self.pending,
            "blocking_conflicts": self.conflicts,
        }

    def apply_profile(self, name):
        self.calls.append(("apply", name))
        if self.apply_exc:
            raise self.apply_exc
        self.current = name

    def rollback(self):
        self.calls.append(("rollback",))
        if self.restore:
            self.current = self.active


def seq(*results):
    it = iter(results)
    return lambda: next(it)


P = pt.Probe


def test_clean_run_is_safe_and_always_rolls_back():
    c = FakeClient()
    r = pt.run_live_test(
        c,
        "publico",
        seq([P("dns", True), P("gateway_v4", None)], [P("dns", True), P("gateway_v4", None)]),
    )
    assert r.verdict == "safe" and r.regressions == [] and r.restored
    assert c.calls == [("apply", "publico"), ("rollback",)]


def test_regression_is_detected():
    c = FakeClient()
    r = pt.run_live_test(
        c,
        "publico",
        seq([P("dns", True), P("loopback_v4", True)], [P("dns", False), P("loopback_v4", True)]),
    )
    assert r.regressions == ["dns"] and r.verdict == "unsafe" and r.restored


def test_failures_that_already_existed_are_not_regressions():
    c = FakeClient()
    r = pt.run_live_test(c, "publico", seq([P("dns", False)], [P("dns", False)]))
    assert r.regressions == [] and r.verdict == "safe"


def test_rollback_happens_even_if_probing_crashes():
    c = FakeClient()
    calls = iter([[P("dns", True)]])

    def probes():
        try:
            return next(calls)
        except StopIteration:
            raise RuntimeError("probe crashed") from None

    with pytest.raises(RuntimeError):
        pt.run_live_test(c, "publico", probes)
    assert c.calls[-1] == ("rollback",)


def test_unrestored_state_is_unsafe():
    c = FakeClient(restore=False)
    r = pt.run_live_test(c, "publico", seq([P("dns", True)], [P("dns", True)]))
    assert not r.restored and r.verdict == "unsafe"


def test_refuses_when_busy_or_conflicting():
    with pytest.raises(FwError) as e:
        pt.run_live_test(FakeClient(pending="casa"), "publico", seq())
    assert e.value.code == "apply_pending"
    with pytest.raises(FwError) as e:
        pt.run_live_test(FakeClient(conflicts=["ufw"]), "publico", seq())
    assert e.value.code == "firewall_conflict"


def test_apply_failure_means_nothing_to_roll_back():
    c = FakeClient(apply_exc=FwError("x", code="nft_failed"))
    with pytest.raises(FwError):
        pt.run_live_test(c, "publico", seq([P("dns", True)]))
    assert ("rollback",) not in c.calls
