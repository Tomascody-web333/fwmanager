"""Races and crashes against the real daemon (private bus, private ufw tree, private netns).

Whatever happens, afterwards ufw must carry exactly the rules of the profile the daemon
reports as active: nothing half applied, nothing doubled.
"""

import random
import signal
import threading
import time

import pytest
from gi.repository import GLib
from helpers.harness import Daemon, ufw_cmd

from fwmanager.client import Client
from fwmanager.errors import FwError

pytestmark = pytest.mark.integration

PROFILES = ["home", "work", "public", "custom"]
EXPECTED = {"apply_pending", "no_pending", "unknown_rule", "profile_too_big"}


def assert_consistent(d):
    st = d.client.status()
    assert st["pending_profile"] == ""
    active = st["active_profile"]
    assert active in PROFILES
    codes = {code for _sev, code, _detail in d.client.check_profile(active)}
    assert not codes & {"will_add", "will_remove", "unparsed_rules"}, codes
    added = ufw_cmd(d.ufw_root, d.prefix, "show", "added").splitlines()
    ours = [ln for ln in added if "fwmanager:" in ln]
    assert len(ours) == len(set(ours)), "a rule was added twice"
    assert all(f"fwmanager:{active}" in ln for ln in ours), ours
    return active


def test_many_clients_at_once(nft, netns_prefix, private_bus, tmp_path, ufw_root):
    d = Daemon(netns_prefix, private_bus, ufw_root, timeout=2, state_dir=tmp_path / "s")
    unexpected, stop = [], time.monotonic() + 35

    def worker(seed):
        r, c = random.Random(seed), Client(private_bus)
        while time.monotonic() < stop:
            action = r.choice(["apply", "apply", "confirm", "rollback", "status", "edit", "rule"])
            try:
                if action == "apply":
                    c.apply_profile(r.choice(PROFILES))
                elif action == "confirm":
                    c.confirm()
                elif action == "rollback":
                    c.rollback()
                elif action == "status":
                    c.status()
                elif action == "edit":
                    c.set_profile("custom", r.sample(["ssh", "web", "samba"], 2),
                                  r.random() < 0.5, r.random() < 0.5, "lan", [])  # fmt: skip
                else:
                    rules = c.list_rules()
                    if rules and r.random() < 0.5:
                        c.remove_rule(r.choice(rules)[0])
                    else:
                        c.add_rule("hide", {"profiles": [r.choice(PROFILES)]})
            except FwError as exc:
                if exc.code not in EXPECTED:
                    unexpected.append((action, exc.code, exc.message))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert unexpected == [], unexpected[:5]
        assert d.proc.poll() is None
        try:
            d.client.rollback()
        except FwError:
            pass
        assert_consistent(d)
    finally:
        log = d.stop()
    assert "Traceback" not in log, log[-2000:]


@pytest.mark.parametrize("delay", [0.05, 0.3, 0.8, 1.5, 3.0])
@pytest.mark.parametrize("during", ["apply", "rollback"])
def test_killed_in_the_middle_of_a_change(
    nft, netns_prefix, private_bus, tmp_path, ufw_root, delay, during
):
    state = tmp_path / "s"
    d = Daemon(netns_prefix, private_bus, ufw_root, timeout=30, state_dir=state)
    d.client.apply_profile("work")
    d.client.confirm()
    target = "home" if delay < 1 else "public"
    if during == "rollback":
        d.client.apply_profile(target)

    def change():
        try:
            c = Client(private_bus)
            c.apply_profile(target) if during == "apply" else c.rollback()
        except (FwError, GLib.Error):
            pass  # the daemon is killed under it

    t = threading.Thread(target=change)
    t.start()
    time.sleep(delay)
    d.proc.send_signal(signal.SIGKILL)
    d.proc.wait(10)
    t.join(30)
    # Restart on the same state: the last *confirmed* profile must come back, whole.
    d2 = Daemon(netns_prefix, private_bus, ufw_root, timeout=30, state_dir=state)
    try:
        assert assert_consistent(d2) == "work"
    finally:
        log = d2.stop()
    assert "Traceback" not in log, log[-2000:]
