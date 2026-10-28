"""Our D-Bus policy file, enforced by a dbus-daemon running the real system-bus defaults.

The group is renamed to ``nogroup`` so no group has to be created: a process running as
``nobody`` with that group stands in for a member of ``fwmanager``; ``daemon`` for anyone else.

Learned the hard way: dbus-daemon matches ``<policy group>`` against the groups the *process*
really has (primary + supplementary, from its credentials), not against /etc/group. So a user
added to the group a minute ago is still refused until the app is started with the new group.
That is why the first-run setup relaunches the app (see ``fwmanager.ui.relaunch``).
"""

import subprocess

import pytest
from helpers import system_bus
from helpers.harness import Daemon

pytestmark = pytest.mark.integration

# Credentials as a login session would give them (uid, primary group, no extras).
MEMBER = {"user": 65534, "group": 65534, "extra_groups": []}  # nobody, group nogroup
OUTSIDER = {"user": 1, "group": 1, "extra_groups": []}  # daemon, group daemon
ROOT_NO_GROUP = {"user": 0, "group": 0, "extra_groups": []}


@pytest.fixture
def sysbus():
    try:
        address, stop = system_bus.start("nogroup")
    except FileNotFoundError:
        pytest.skip("no system bus configuration on this machine")
    yield address
    stop()


@pytest.fixture
def daemon(nft, netns_prefix, sysbus, ufw_root, tmp_path):
    d = Daemon(netns_prefix, sysbus, ufw_root, group="nogroup", timeout=20,
               state_dir=tmp_path / "state")  # fmt: skip
    yield d
    d.stop()


def busctl(bus, creds, member, *args, dest="org.fwmanager.Control"):
    return subprocess.run(
        ["busctl", f"--address={bus}", "call", dest, "/org/fwmanager/Control",
         "org.fwmanager.Control", member, *args],
        capture_output=True, text=True, timeout=15, **creds,
    )  # fmt: skip


def test_the_real_bus_refuses_what_the_default_policy_denies(sysbus):
    """Sanity of the harness: a name nobody has a policy for cannot be called at all."""
    r = busctl(sysbus, MEMBER, "GetStatus", dest="org.example.Nothing")
    assert r.returncode != 0


def test_group_member_gets_through(daemon):
    r = busctl(daemon.bus, MEMBER, "ListProfiles")
    assert r.returncode == 0, r.stderr
    assert "home" in r.stdout


def test_other_accounts_are_stopped_by_the_bus_itself(daemon):
    r = busctl(daemon.bus, OUTSIDER, "GetStatus")
    assert r.returncode != 0
    assert "denied" in r.stderr.lower() or "rejected" in r.stderr.lower()
    log = daemon.stop()
    assert "from uid 1" not in log  # it never even reached the daemon's own check


def test_root_needs_the_group_too(daemon):
    """The reason dev-install adds root to the group: the NetworkManager hook runs as root."""
    r = busctl(daemon.bus, ROOT_NO_GROUP, "GetStatus")
    assert r.returncode != 0 and "denied" in r.stderr.lower()


OWN_NAME_SCRIPT = """
from gi.repository import Gio, GLib
conn = Gio.DBusConnection.new_for_address_sync(
    "@BUS@",
    Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
    | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
loop = GLib.MainLoop()
def got(*a): print("owned"); loop.quit()
def lost(*a): print("refused"); loop.quit()
Gio.bus_own_name_on_connection(
    conn, "org.fwmanager.Control", Gio.BusNameOwnerFlags.DO_NOT_QUEUE, got, lost)
GLib.timeout_add_seconds(5, loop.quit)
loop.run()
"""


def try_to_own_the_name(bus, **kwargs):
    return subprocess.run(
        ["/usr/bin/python3.12", "-c", OWN_NAME_SCRIPT.replace("@BUS@", bus)],
        capture_output=True, text=True, timeout=30, **kwargs,
    )  # fmt: skip


def test_only_root_can_own_the_name(sysbus):
    assert try_to_own_the_name(sysbus).stdout.strip() == "owned"


def test_an_impostor_cannot_take_the_name(sysbus):
    r = try_to_own_the_name(sysbus, env={"PATH": "/usr/bin:/bin"}, **MEMBER)
    assert r.stdout.strip() != "owned", "a non-root account must never own org.fwmanager.Control"


SIGNAL_SCRIPT = """
import sys
sys.path.insert(0, "@SRC@")
from gi.repository import GLib
from fwmanager.client import Client
c = Client("@BUS@")
got = []
loop = GLib.MainLoop()
def on(name, args):
    got.append(name)
    if name == "Applied": loop.quit()
c.subscribe(on)
c.apply_profile("home")
GLib.timeout_add_seconds(15, loop.quit)
loop.run()
c.rollback()
print("signals:", ",".join(got))
"""


def test_members_receive_the_daemons_signals(daemon, src_for_others):
    script = SIGNAL_SCRIPT.replace("@SRC@", str(src_for_others)).replace("@BUS@", daemon.bus)
    r = subprocess.run(
        ["/usr/bin/python3.12", "-c", script], capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin"}, timeout=60, **MEMBER,
    )  # fmt: skip
    if "Permission denied" in r.stderr or "No module" in r.stderr:
        pytest.skip("the unprivileged user cannot read the source tree here")
    assert "signals: Applied" in r.stdout, r.stdout + r.stderr


def test_unprivileged_members_can_use_the_whole_api(daemon):
    """Not only reads: a member can apply, and the daemon's own checks accept the uid."""
    r = busctl(daemon.bus, MEMBER, "GetStatus")
    assert r.returncode == 0 and "active_profile" in r.stdout
    r = busctl(daemon.bus, MEMBER, "ApplyProfile", "s", "work")
    assert r.returncode == 0, r.stderr
    r = busctl(daemon.bus, MEMBER, "Rollback")
    assert r.returncode == 0, r.stderr


def test_a_supplementary_group_is_enough(daemon):
    """What `usermod -aG fwmanager me` gives after the next login."""
    creds = {"user": 65534, "group": 65534, "extra_groups": [65534]}
    # (65534 stands in for the real group; the point is a *supplementary* entry matches too)
    assert busctl(daemon.bus, creds, "ListProfiles").returncode == 0


def test_without_the_group_in_the_process_the_bus_says_no(daemon):
    """Added to /etc/group is not enough: the running process must have been started with it."""
    process_without_group = {"user": 65534, "group": 1, "extra_groups": []}
    r = busctl(daemon.bus, process_without_group, "ListProfiles")
    assert r.returncode != 0 and "denied" in r.stderr.lower()
