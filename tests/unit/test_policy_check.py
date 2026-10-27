import re

import pytest

from fwmanager.core import policy_check as pc
from fwmanager.core.conflicts import Conflicts
from fwmanager.core.profiles import DEFAULT_PROFILES, Profile, Source
from fwmanager.core.rules import RenderOptions, render
from fwmanager.core.services import load_builtin

CAT = load_builtin()


def script(name="publico", **opts):
    return render(DEFAULT_PROFILES[name], CAT, RenderOptions(**opts))


def codes(report, severity=None):
    return {f.code for f in report.findings if severity in (None, f.severity)}


def drop_lines(text, pattern):
    kept = [ln for ln in text.splitlines() if not re.search(pattern, ln)]
    assert len(kept) < len(text.splitlines()), f"pattern {pattern!r} matched nothing"
    return "\n".join(kept) + "\n"


@pytest.mark.parametrize("name", list(DEFAULT_PROFILES))
@pytest.mark.parametrize("router", [False, True])
@pytest.mark.parametrize("log", [False, True])
def test_every_default_profile_passes(name, router, log):
    r = pc.check_profile(
        DEFAULT_PROFILES[name], CAT, RenderOptions(router_mode=router, log_blocked=log)
    )
    assert codes(r, pc.ERROR) == set(), r.findings
    assert r.verdict in ("safe", "warnings")
    if not router:
        assert r.verdict == "safe"


# Each mutation removes one thing a normal machine needs; the check must call it out.
@pytest.mark.parametrize(
    "pattern,expected",
    [
        (r"ct state established", "missing_established"),
        (r"ct state invalid", "missing_invalid_drop"),
        (r'iif "lo"', "missing_loopback"),
        (r"udp sport 67", "missing_dhcp4"),
        (r"udp sport 547", "missing_dhcp6"),
        (r"nd-router-advert", "missing_nd_router_advert"),
        (r"ip6 hoplimit 255", "missing_nd_neighbor_solicit"),
        (r"packet-too-big", "missing_packet_too_big"),
    ],
)
def test_missing_essentials_are_errors(pattern, expected):
    r = pc.analyze_script(drop_lines(script(), pattern))
    assert expected in codes(r, pc.ERROR)
    assert r.verdict == "unsafe"


def test_output_policy_drop_is_an_error():
    bad = script().replace(
        "hook output priority filter; policy accept", "hook output priority filter; policy drop"
    )
    assert "output_blocked" in codes(pc.analyze_script(bad), pc.ERROR)


def test_output_drop_rule_is_an_error():
    bad = script().replace("chain output {\n", "chain output {\n\t\ttcp dport 443 drop\n")
    assert "output_blocked" in codes(pc.analyze_script(bad), pc.ERROR)


def test_forward_drop_outside_router_mode_is_an_error():
    bad = script(router_mode=True)
    assert "forward_blocked" in codes(pc.analyze_script(bad, expect_router=False), pc.ERROR)
    ok = pc.analyze_script(bad, expect_router=True)
    assert "forward_blocked" not in codes(ok) and "router_mode" in codes(ok, pc.INFO)


def test_input_policy_not_drop_is_a_warning():
    bad = script().replace(
        "hook input priority filter; policy drop", "hook input priority filter; policy accept"
    )
    r = pc.analyze_script(bad)
    assert "input_not_dropping" in codes(r, pc.WARNING) and r.verdict == "warnings"


@pytest.mark.parametrize(
    "extra",
    [
        "flush ruleset\n",
        "table ip nat {\n}\n",
        "add table ip filter\n",
        "delete table inet docker\n",
        "flush table inet fwmanager\n",
    ],
)
def test_touching_other_tables_is_an_error(extra):
    r = pc.analyze_script(extra + script())
    assert r.verdict == "unsafe"


@pytest.mark.parametrize(
    "verb", ["reject", "masquerade", "dnat to 1.2.3.4", "redirect", "jump foo"]
)
def test_forbidden_verbs(verb):
    bad = script().replace("chain forward {\n", f"chain forward {{\n\t\ttcp dport 1 {verb}\n")
    assert "forbidden_verb" in codes(pc.analyze_script(bad), pc.ERROR)


def test_non_atomic_script_is_an_error():
    bad = drop_lines(script(), r"^delete table")
    assert "not_atomic" in codes(pc.analyze_script(bad), pc.ERROR)


def test_missing_chains():
    assert "no_input_chain" in codes(pc.analyze_script("add table inet fwmanager\n"), pc.ERROR)


def test_exposure_warning():
    p = Profile("x", services=("ssh",), source=Source("any"))
    r = pc.check_profile(p, CAT)
    assert "exposed_to_any" in codes(r, pc.WARNING) and r.verdict == "warnings"
    wide = Profile("x", services=("ssh",), source=Source("cidrs", ("0.0.0.0/0",)))
    assert "exposed_to_any" in codes(pc.check_profile(wide, CAT), pc.WARNING)
    assert pc.check_profile(Profile("x"), CAT).verdict == "safe"


def test_conflicts_in_report():
    r = pc.check_profile(DEFAULT_PROFILES["casa"], CAT, conflicts=Conflicts(("ufw",), ("docker",)))
    assert "firewall_conflict" in codes(r, pc.ERROR) and "docker_present" in codes(r, pc.INFO)
    r2 = pc.check_profile(
        DEFAULT_PROFILES["casa"],
        CAT,
        RenderOptions(router_mode=True),
        conflicts=Conflicts((), ("docker",)),
    )
    assert "docker_present" in codes(r2, pc.WARNING)


def test_invalid_profile_reports_instead_of_raising():
    r = pc.check_profile(Profile("x", services=("nope",)), CAT)
    assert "unknown_service" in codes(r, pc.ERROR)


class FakeNft:
    def __init__(self, exc=None):
        self.exc, self.calls = exc, []

    def run(self, args, stdin=None):
        self.calls.append((args, stdin))
        if self.exc:
            raise self.exc


def test_nft_dry_run_is_used_and_failures_reported():
    from fwmanager.errors import NftError

    nft = FakeNft()
    pc.check_profile(DEFAULT_PROFILES["casa"], CAT, nft=nft)
    assert nft.calls[0][0] == ["-c", "-f", "-"]  # check mode: never changes the ruleset
    r = pc.check_profile(DEFAULT_PROFILES["casa"], CAT, nft=FakeNft(NftError("syntax error")))
    assert "nft_rejects_rules" in codes(r, pc.ERROR)


# -- SSH self-lockout --------------------------------------------------------


def ssh(profile, conn, catalog=CAT):
    return pc.ssh_findings(profile, catalog, {"SSH_CONNECTION": conn} if conn else {})


def test_no_ssh_session_no_finding():
    assert ssh(DEFAULT_PROFILES["publico"], None) == []
    assert ssh(DEFAULT_PROFILES["publico"], "garbage") == []
    assert ssh(DEFAULT_PROFILES["publico"], "not-an-ip 1 2 x") == []


def test_ssh_blocked_by_publico():
    f = ssh(DEFAULT_PROFILES["publico"], "192.168.1.50 51000 192.168.1.2 22")
    assert [x.code for x in f] == ["ssh_reconnect_blocked"] and f[0].severity == pc.WARNING


def test_ssh_allowed_from_lan_in_casa():
    assert ssh(DEFAULT_PROFILES["casa"], "192.168.1.50 51000 192.168.1.2 22")[0].code == "ssh_ok"


def test_ssh_from_outside_lan_blocked_by_lan_source():
    f = ssh(DEFAULT_PROFILES["casa"], "203.0.113.9 51000 192.168.1.2 22")
    assert f[0].code == "ssh_reconnect_blocked"


def test_ssh_on_nonstandard_port_is_judged_by_that_port():
    assert ssh(DEFAULT_PROFILES["casa"], "192.168.1.50 51000 192.168.1.2 2222")[0].code == (
        "ssh_reconnect_blocked"
    )


def test_ssh_with_cidr_source_and_ipv6():
    p = Profile("x", services=("ssh",), source=Source("cidrs", ("203.0.113.0/24", "2001:db8::/32")))
    assert ssh(p, "203.0.113.9 1 10.0.0.1 22")[0].code == "ssh_ok"
    assert ssh(p, "2001:db8::5 1 2001:db8::1 22")[0].code == "ssh_ok"
    assert ssh(p, "198.51.100.1 1 10.0.0.1 22")[0].code == "ssh_reconnect_blocked"
    lan6 = ssh(DEFAULT_PROFILES["casa"], "fe80::1 1 fe80::2 22")
    assert lan6[0].code == "ssh_ok"
