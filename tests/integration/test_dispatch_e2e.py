"""NetworkManager dispatcher flow against the real daemon and ufw (inside a netns)."""

import json
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from helpers.harness import (
    ROOT,
    Daemon,
    bring_lo_up,
    run_cli_in_ns,
)

from fwmanager.cli import dispatch as d
from fwmanager.client import Client

pytestmark = pytest.mark.integration

UUID = "123e4567-e89b-12d3-a456-426614174000"
OTHER = "223e4567-e89b-12d3-a456-426614174000"


@pytest.fixture
def env(nft, netns_prefix, private_bus, tmp_path, ufw_root):
    bring_lo_up(netns_prefix)
    daemon = Daemon(netns_prefix, private_bus, ufw_root, timeout=20, state_dir=tmp_path / "state")
    daemon.state_dir = tmp_path / "state"
    yield daemon
    daemon.stop()


def dispatch_cli(env, netns_prefix, uuid, gateway="127.0.0.1", action="up"):
    return run_cli_in_ns(
        netns_prefix,
        env.bus,
        "dispatch",
        "lo",
        action,
        env={"CONNECTION_UUID": uuid, "IP4_GATEWAY": gateway},
    )


def active(env):
    return env.client.status()["active_profile"]


def test_mapped_network_switches_profile_and_persists(env, netns_prefix):
    env.client.set_network_profile("", UUID, "home")
    r = dispatch_cli(env, netns_prefix, UUID)
    assert r.returncode == 0, r.stderr
    assert active(env) == "home" and env.client.status()["pending_profile"] == ""
    assert "confirmed" in r.stderr
    assert json.loads((env.state_dir / "state.json").read_text())["profile"] == "home"


def test_unknown_network_gets_publico(env, netns_prefix):
    env.client.set_network_profile("", UUID, "home")
    dispatch_cli(env, netns_prefix, UUID)
    r = dispatch_cli(env, netns_prefix, OTHER)  # a network nobody mapped
    assert r.returncode == 0, r.stderr
    assert active(env) == "public"


def test_switching_back_and_forth(env, netns_prefix):
    env.client.set_network_profile("", UUID, "home")
    env.client.set_network_profile("", OTHER, "work")
    for uuid, expected in ((UUID, "home"), (OTHER, "work"), (UUID, "home")):
        assert dispatch_cli(env, netns_prefix, uuid).returncode == 0
        assert active(env) == expected


def test_same_profile_is_a_noop(env, netns_prefix):
    env.client.set_network_profile("", UUID, "home")
    dispatch_cli(env, netns_prefix, UUID)
    r = dispatch_cli(env, netns_prefix, UUID)
    assert r.returncode == 0 and "already active" in r.stderr


@pytest.mark.parametrize("action", ["down", "vpn-up", "dhcp4-change", "connectivity-change"])
def test_other_actions_are_ignored(env, netns_prefix, action):
    env.client.set_network_profile("", UUID, "home")
    r = dispatch_cli(env, netns_prefix, UUID, action=action)
    assert r.returncode == 0 and active(env) == "public"


def test_unverifiable_gateway_still_confirms(env, netns_prefix):
    env.client.set_network_profile("", UUID, "home")
    r = dispatch_cli(env, netns_prefix, UUID, gateway="10.255.255.1")  # unroutable here
    assert r.returncode == 0, r.stderr
    assert "cannot verify" in r.stderr and active(env) == "home"


def test_malicious_environment_is_ignored(env, netns_prefix):
    r = run_cli_in_ns(
        netns_prefix,
        env.bus,
        "dispatch",
        "lo",
        "up",
        env={"CONNECTION_UUID": 'x"; flush ruleset', "IP4_GATEWAY": "1.1.1.1; reboot"},
    )
    assert r.returncode == 0, r.stderr
    assert active(env) == "public"
    bad_iface = run_cli_in_ns(netns_prefix, env.bus, "dispatch", "lo;reboot", "up")
    assert bad_iface.returncode == 1


def test_lost_connectivity_rolls_back_for_real(env):
    """Real daemon + real ufw, with a pinger that sees the gateway die after the change."""
    env.client.set_network_profile("", UUID, "home")
    before = env.fp()

    def ping(addr, iface, timeout):
        return env.fp() == before  # reachable only while the old rules are in place

    ctx = d.Context("lo", UUID, (d.Gateway("192.168.1.1", 4),))
    code = d.dispatch(Client(env.bus), ctx, ping=ping, sleep=lambda s: None)
    assert code == d.EXIT_REVERTED
    assert env.fp() == before
    assert env.client.status()["pending_profile"] == ""
    assert not (env.state_dir / "state.json").exists()  # a reverted profile is never saved


def test_real_icmp_echo_over_loopback(env, netns_prefix):
    r = subprocess.run(
        [
            *netns_prefix,
            "python3.12" if shutil.which("python3.12") else "python3",
            "-c",
            "from fwmanager.cli.dispatch import icmp_echo;"
            "print(icmp_echo('127.0.0.1','lo',2), icmp_echo('10.255.255.1','lo',0.5))",
        ],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
    )
    assert r.stdout.split() == ["True", "False"], r.stderr


# -- the NetworkManager shim script ---


@pytest.fixture
def shim(tmp_path):
    """The real dispatcher script, pointed at a stub that records its arguments."""
    log = tmp_path / "calls.log"
    stub = tmp_path / "fwmanager-cli"
    stub.write_text(f'#!/bin/sh\necho "$@" >> {log}\n')
    stub.chmod(0o755)
    src = (ROOT / "data/networkmanager/90-fwmanager").read_text()
    script = tmp_path / "90-fwmanager"
    script.write_text(src.replace("/usr/local/bin/fwmanager-cli", str(stub)))
    script.chmod(0o755)

    def call(iface, action):
        subprocess.run([str(script), iface, action], check=True)
        return log.read_text().splitlines() if log.exists() else []

    return call


def test_shim_only_reacts_to_up(shim):
    assert shim("wlan0", "down") == []
    assert shim("wlan0", "vpn-up") == []
    assert shim("lo", "up") == []
    assert shim("wlan0", "up") == ["dispatch wlan0 up"]


def test_shim_file_is_nm_compatible():
    path = ROOT / "data/networkmanager/90-fwmanager"
    mode = path.stat().st_mode
    assert mode & stat.S_IXUSR and not mode & (stat.S_IWGRP | stat.S_IWOTH)
    assert path.read_text().startswith("#!/bin/sh")
    assert Path(path).name.startswith("90-")
