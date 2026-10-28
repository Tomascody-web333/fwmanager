"""What each profile really lets through: packets between two hosts, not just rules on paper."""

import subprocess
import sys

import pytest
from helpers import lab as labmod
from helpers.harness import Daemon, run_cli_in_ns, wait_until
from helpers.lab import A_LAN, A_OUT, B_LAN, B_OUT, LONG_CONNECTION, Lab

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def lab(netns_prefix):
    import shutil

    if not shutil.which("ip"):
        pytest.skip("iproute2 is not installed")
    try:
        lab = Lab(netns_prefix)
    except AssertionError as exc:
        pytest.skip(f"cannot build the veth lab here: {exc}")
    yield lab
    lab.close()


@pytest.fixture
def host(lab, nft, netns_prefix, private_bus, ufw_root, tmp_path):
    """The firewalled host with the daemon running, plus listeners on SSH-like and web ports."""
    d = Daemon(netns_prefix, private_bus, ufw_root, timeout=30, state_dir=tmp_path / "state")
    lab.listen_on_a(22)
    lab.listen_on_a(80)
    yield d
    for p in lab.listeners:
        p.kill()
        p.wait()
    lab.listeners.clear()
    d.stop()


def use(host, profile):
    host.client.apply_profile(profile)
    host.client.confirm()


def ssh(lab, src, dst):
    return lab.tcp(src, dst, 22)


def test_the_lab_itself_works_without_a_firewall(lab, nft, netns_prefix):
    """Control experiment: with nothing filtering, B reaches A. Otherwise the rest means nothing."""
    p = lab.listen_on_a(22)
    try:
        assert ssh(lab, B_LAN, A_LAN) == "ok" and ssh(lab, B_OUT, A_OUT) == "ok"
        assert lab.ping(A_LAN) == "True"
    finally:
        p.kill()
        p.wait()
        lab.listeners.remove(p)


def test_public_lets_nothing_in(lab, host):
    assert host.client.status()["active_profile"] == "public"  # first boot
    assert ssh(lab, B_LAN, A_LAN) == "fail"
    assert ssh(lab, B_OUT, A_OUT) == "fail"
    assert lab.tcp(B_LAN, A_LAN, 80) == "fail"
    assert lab.ping(A_LAN) == "False"  # and it does not answer ping


def test_home_lets_ssh_in_from_the_local_network_only(lab, host):
    use(host, "home")
    assert ssh(lab, B_LAN, A_LAN) == "ok"  # a neighbour on the local network
    assert ssh(lab, B_OUT, A_OUT) == "fail"  # the same neighbour from an outside address
    assert lab.tcp(B_LAN, A_LAN, 80) == "fail"  # web is not part of "home"
    assert lab.ping(A_LAN) == "True"  # casa answers ping


def test_work_opens_ssh_but_hides_from_mdns_and_still_answers_ping(lab, host):
    use(host, "work")
    assert ssh(lab, B_LAN, A_LAN) == "ok" and ssh(lab, B_OUT, A_OUT) == "fail"
    assert lab.ping(A_LAN) == "True"


def test_switching_back_to_public_closes_what_home_opened(lab, host):
    use(host, "home")
    assert ssh(lab, B_LAN, A_LAN) == "ok"
    use(host, "public")
    assert ssh(lab, B_LAN, A_LAN) == "fail"
    assert lab.ping(A_LAN) == "False"


def test_a_rule_opens_exactly_what_its_sentence_says(lab, host):
    host.client.add_rule("allow_only_in", {"service": "web", "profiles": ["home"]})
    use(host, "home")
    assert lab.tcp(B_LAN, A_LAN, 80) == "ok" and lab.tcp(B_OUT, A_OUT, 80) == "fail"
    use(host, "work")
    assert lab.tcp(B_LAN, A_LAN, 80) == "fail"  # "only in casa"
    host.client.add_rule("restrict_source", {"scope": "cidrs", "cidrs": [f"{B_OUT}/32"],
                                             "profiles": ["work"]})  # fmt: skip
    use(host, "work")
    assert ssh(lab, B_OUT, A_OUT) == "ok" and ssh(lab, B_LAN, A_LAN) == "fail"  # only that address


def test_a_hide_rule_silences_ping(lab, host):
    use(host, "home")
    assert lab.ping(A_LAN) == "True"
    host.client.add_rule("hide", {"profiles": ["home"]})
    use(host, "home")
    assert lab.ping(A_LAN) == "False" and ssh(lab, B_LAN, A_LAN) == "ok"


def test_connections_already_open_survive_a_profile_change(lab, host):
    use(host, "home")
    proc = subprocess.Popen(
        [*lab.b, sys.executable, "-c", LONG_CONNECTION, B_LAN, A_LAN, "22", "4"],
        stdout=subprocess.PIPE, text=True,
    )  # fmt: skip
    assert proc.stdout.readline().strip() == "round 0 ok"
    use(host, "public")  # SSH is now closed for *new* connections...
    assert ssh(lab, B_LAN, A_LAN) == "fail"
    assert proc.stdout.readline().strip() == "round 1 ok"  # ...but this one carries on
    proc.wait(10)


def test_an_unconfirmed_change_really_reopens_the_door(
    lab, nft, netns_prefix, private_bus, ufw_root, tmp_path
):
    d = Daemon(netns_prefix, private_bus, ufw_root, timeout=3, state_dir=tmp_path / "state")
    lab.listen_on_a(22)
    try:
        use(d, "home")
        assert ssh(lab, B_LAN, A_LAN) == "ok"
        d.client.apply_profile("public")  # nobody confirms
        assert ssh(lab, B_LAN, A_LAN) == "fail"
        assert wait_until(lambda: ssh(lab, B_LAN, A_LAN) == "ok", 20)  # the timer put casa back
    finally:
        for p in lab.listeners:
            p.kill()
            p.wait()
        lab.listeners.clear()
        d.stop()


# -- the live policy test, against a gateway that really answers ---


@pytest.fixture
def gateway(lab):
    lab.set_default_route_on_a()
    yield B_LAN
    lab.clear_default_route_on_a()


def test_live_policy_test_reaches_the_real_gateway_before_and_after(
    lab, host, gateway, netns_prefix
):
    r = run_cli_in_ns(netns_prefix, host.bus, "test-policy", "home")
    assert r.returncode in (0, 2), r.stdout + r.stderr
    assert "Reach the IPv4 gateway: ok -> ok" in r.stdout
    assert "UNSAFE" not in r.stdout


def test_live_policy_test_catches_a_profile_that_cuts_the_connection(
    lab, host, gateway, ufw_root, netns_prefix
):
    """Damage ufw so replies to our own traffic are dropped, skip the static check, run live.

    The static analysis would refuse this configuration outright; the live test is the second
    line of defence. The damaged file is only on disk (the kernel still has good rules), so the
    "before" measurement is healthy; applying "home" reloads ufw, the gateway goes silent, and the
    test must notice and report the profile as unsafe.
    """
    from fwmanager import policytest as pt

    path = ufw_root / "etc/ufw/before.rules"
    good = path.read_text()
    path.write_text(
        good.replace(
            "--ctstate RELATED,ESTABLISHED -j ACCEPT", "--ctstate RELATED,ESTABLISHED -j DROP"
        )
    )
    assert path.read_text() != good

    def probes():
        script = (
            f"import sys; sys.path.insert(0, {labmod._src()!r})\n"
            "from fwmanager import policytest as pt\n"
            "print(pt.probe_gateways(4, pt.default_gateways_v4()).ok)"
        )
        r = subprocess.run(
            [*netns_prefix, sys.executable, "-c", script],
            capture_output=True, text=True, timeout=30,
        )  # fmt: skip
        return [pt.Probe("gateway_v4", {"True": True, "False": False}.get(r.stdout.strip()))]

    try:
        result = pt.run_live_test(host.client, "home", probes)
        assert result.before[0].ok is True  # healthy before the change
        assert result.after[0].ok is False  # silent after it
        assert result.regressions == ["gateway_v4"] and result.verdict == "unsafe"
        assert host.client.status()["pending_profile"] == ""  # and the test cleaned up after itself
    finally:
        path.write_text(good)
