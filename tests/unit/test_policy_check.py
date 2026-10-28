import pytest

from fwmanager.core import policy_check as pc
from fwmanager.core import ufw_files
from fwmanager.core.profiles import DEFAULT_PROFILES, Profile, Source
from fwmanager.core.services import load_builtin

CAT = load_builtin()


def codes(report, severity=None):
    return {f.code for f in report.findings if severity in (None, f.severity)}


# -- ufw's own configuration must keep the essentials ---


def test_pristine_ufw_config_has_everything_a_client_needs(ufw_root):
    r = pc.analyze_config(ufw_root / "etc/ufw", ipv6=True)
    assert r.findings == [], r.findings


def edit(ufw_root, name, pattern):
    path = ufw_root / "etc/ufw" / name
    lines = path.read_text().splitlines(keepends=True)
    kept = [ln for ln in lines if pattern not in ln or ln.lstrip().startswith("#")]
    assert len(kept) < len(lines), f"{pattern!r} matched nothing in {name}"
    path.write_text("".join(kept))


@pytest.mark.parametrize(
    "name,pattern,expected",
    [
        ("before.rules", "-i lo -j ACCEPT", "missing_loopback"),
        ("before.rules", "--ctstate RELATED,ESTABLISHED", "missing_established"),
        ("before.rules", "--sport 67", "missing_dhcp4"),
        ("before.rules", "--icmp-type destination-unreachable", "missing_destination_unreachable"),
        ("before6.rules", "--sport 547", "missing_dhcp6"),
        ("before6.rules", "neighbor-solicitation", "missing_nd_neighbor_solicit"),
        ("before6.rules", "neighbor-advertisement", "missing_nd_neighbor_advert"),
        ("before6.rules", "router-advertisement", "missing_nd_router_advert"),
        ("before6.rules", "packet-too-big", "missing_packet_too_big"),
    ],
)
def test_missing_essentials_are_errors(ufw_root, name, pattern, expected):
    edit(ufw_root, name, pattern)
    r = pc.analyze_config(ufw_root / "etc/ufw", ipv6=True)
    assert expected in codes(r, pc.ERROR) and r.verdict == "unsafe"


def test_ipv6_files_ignored_when_ipv6_is_off(ufw_root):
    edit(ufw_root, "before6.rules", "neighbor-solicitation")
    assert pc.analyze_config(ufw_root / "etc/ufw", ipv6=False).verdict == "safe"


def test_missing_anchor_means_unsupported(ufw_root):
    edit(ufw_root, "before.rules", "--ctstate INVALID -j DROP")
    r = pc.analyze_config(ufw_root / "etc/ufw", ipv6=False)
    assert "ufw_unsupported" in codes(r, pc.ERROR)


def test_our_block_may_only_drop_ping_and_mdns(ufw_root):
    d = ufw_root / "etc/ufw"
    ufw_files.apply_block(d, "public", ping=False, mdns=False, v6=True)
    assert pc.analyze_config(d, ipv6=True).findings == []
    path = d / "before.rules"
    path.write_text(
        path.read_text().replace(
            ufw_files.END, "-A ufw-before-input -p tcp -j DROP\n" + ufw_files.END
        )
    )
    r = pc.analyze_config(d, ipv6=True)
    assert "foreign_statement" in codes(r, pc.ERROR)


def test_a_block_cannot_hide_a_missing_essential(ufw_root):
    """Essentials are judged on the file *without* our block."""
    d = ufw_root / "etc/ufw"
    ufw_files.apply_block(d, "public", ping=True, mdns=True, v6=False)
    path = d / "before.rules"
    loopback = "-A ufw-before-input -i lo -j ACCEPT\n"
    text = path.read_text()
    # Move the real loopback rule into the managed block, so only the block still has it.
    text = text.replace(loopback, "", 1).replace(ufw_files.END, loopback + ufw_files.END)
    path.write_text(text)
    assert "missing_loopback" in codes(pc.analyze_config(d, ipv6=False), pc.ERROR)


# -- exposure and SSH ---


def test_exposure_warning():
    p = Profile("x", services=("ssh",), source=Source("any"))
    assert [f.code for f in pc.exposure_findings(p)] == ["exposed_to_any"]
    wide = Profile("x", services=("ssh",), source=Source("cidrs", ("0.0.0.0/0",)))
    assert [f.code for f in pc.exposure_findings(wide)] == ["exposed_to_any"]
    assert pc.exposure_findings(Profile("x")) == []
    assert pc.exposure_findings(DEFAULT_PROFILES["home"]) == []


def ssh(profile, conn):
    return pc.ssh_findings(profile, CAT, {"SSH_CONNECTION": conn} if conn else {})


def test_no_ssh_session_no_finding():
    assert ssh(DEFAULT_PROFILES["public"], None) == []
    assert ssh(DEFAULT_PROFILES["public"], "garbage") == []
    assert ssh(DEFAULT_PROFILES["public"], "not-an-ip 1 2 x") == []


def test_ssh_blocked_by_publico():
    f = ssh(DEFAULT_PROFILES["public"], "192.168.1.50 51000 192.168.1.2 22")
    assert [x.code for x in f] == ["ssh_reconnect_blocked"] and f[0].severity == pc.WARNING


def test_ssh_allowed_from_lan_in_casa():
    assert ssh(DEFAULT_PROFILES["home"], "192.168.1.50 51000 192.168.1.2 22")[0].code == "ssh_ok"


def test_ssh_from_outside_lan_blocked_by_lan_source():
    f = ssh(DEFAULT_PROFILES["home"], "203.0.113.9 51000 192.168.1.2 22")
    assert f[0].code == "ssh_reconnect_blocked"


def test_ssh_on_nonstandard_port_is_judged_by_that_port():
    f = ssh(DEFAULT_PROFILES["home"], "192.168.1.50 51000 192.168.1.2 2222")
    assert f[0].code == "ssh_reconnect_blocked"


def test_ssh_with_cidr_source_and_ipv6():
    p = Profile("x", services=("ssh",), source=Source("cidrs", ("203.0.113.0/24", "2001:db8::/32")))
    assert ssh(p, "203.0.113.9 1 10.0.0.1 22")[0].code == "ssh_ok"
    assert ssh(p, "2001:db8::5 1 2001:db8::1 22")[0].code == "ssh_ok"
    assert ssh(p, "198.51.100.1 1 10.0.0.1 22")[0].code == "ssh_reconnect_blocked"
    assert ssh(DEFAULT_PROFILES["home"], "fe80::1 1 fe80::2 22")[0].code == "ssh_ok"
