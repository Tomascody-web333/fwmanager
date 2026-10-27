import subprocess
import sys

import pytest
from helpers.harness import ROOT

from fwmanager.ui.netinfo import CurrentNetwork, current_network

pytestmark = pytest.mark.integration
UUID = "123e4567-e89b-12d3-a456-426614174000"


@pytest.fixture
def fake_nm(private_bus):
    procs = []

    def start(*args):
        p = subprocess.Popen(
            [sys.executable, str(ROOT / "tests/helpers/fake_nm.py"), private_bus, *args],
            stdout=subprocess.PIPE,
            text=True,
        )
        assert p.stdout.readline().strip() == "ready"
        procs.append(p)

    yield start
    for p in procs:
        p.kill()
        p.wait()


def test_wifi_connection(fake_nm, private_bus):
    fake_nm("802-11-wireless", "Casa WiFi (auto)", UUID, "Casa ☕ WiFi")
    got = current_network(private_bus)
    assert got == CurrentNetwork("Casa WiFi (auto)", "Casa ☕ WiFi", UUID, "802-11-wireless")
    assert got.is_wifi


def test_ethernet_has_no_ssid(fake_nm, private_bus):
    fake_nm("802-3-ethernet", "Wired connection 1", UUID)
    got = current_network(private_bus)
    assert got.ssid == "" and got.uuid == UUID and not got.is_wifi


def test_offline(fake_nm, private_bus):
    fake_nm("none", "", "")
    assert current_network(private_bus) is None


def test_no_network_manager_at_all(private_bus):
    assert current_network(private_bus) is None
