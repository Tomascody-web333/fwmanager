"""Settings → Notifications, and what the running app actually sends."""

import pytest
from helpers.shot import pump
from test_language import localedir, make_app  # noqa: F401
from test_windows import daemon, env  # noqa: F401

pytestmark = pytest.mark.ui


def test_one_switch_per_kind_of_event(env):  # noqa: F811
    rows = env.win.settings.notify_rows
    assert list(rows) == ["reverted", "switched", "blocked", "allowed"]
    assert rows["reverted"].get_active() and rows["switched"].get_active()
    assert not rows["blocked"].get_active() and not rows["allowed"].get_active()


def test_traffic_notifications_turn_recording_on_when_needed(env):  # noqa: F811
    s = env.win.settings
    s.load()
    pump(0.1)
    s.notify_rows["blocked"].set_active(True)
    assert env.daemon.options["log_level"] == "low"  # nothing would be logged otherwise
    s.notify_rows["allowed"].set_active(True)
    assert env.daemon.options["log_level"] == "medium"
    s.notify_rows["allowed"].set_active(False)
    s.notify_rows["blocked"].set_active(True)
    assert env.daemon.options["log_level"] == "medium"  # never lowered
    assert ("blocked", True) in env.changes["notifications"]


def test_a_profile_switched_by_the_network_is_announced(make_app):  # noqa: F811
    app = make_app(user_lang="en")
    app.daemon.signal_cb("Confirmed", ("casa",))  # e.g. the NetworkManager hook confirmed it
    assert app.sent == [("switched", "Now using the Home profile.")]


def test_your_own_switch_is_not_announced(make_app):  # noqa: F811
    app = make_app(user_lang="en")
    app.controller.apply("trabalho")
    app.daemon.signal_cb("Confirmed", ("trabalho",))
    assert app.sent == []


def test_switched_off_means_silent(make_app):  # noqa: F811
    app = make_app(user_lang="en")
    app.set_notifications("switched", False)
    app.daemon.signal_cb("Confirmed", ("casa",))
    app.daemon.signal_cb("Reverted", ("trabalho",))
    assert app.sent == [("reverted", "Work was undone because it was not confirmed in time.")]


def test_new_blocked_attempts_are_announced_old_ones_are_not(make_app):  # noqa: F811
    app = make_app(user_lang="en")
    app.set_notifications("blocked", True)
    app.poll_traffic()  # the baseline: what was already in the log
    assert app.sent == []
    app.daemon.blocked.insert(0, ("t9", "wlan0", "198.51.100.7", "192.168.1.5", "TCP", "445"))
    app.poll_traffic()
    assert app.sent == [("blocked", "198.51.100.7 tried to reach port 445.")]


def test_allowed_connections_only_when_asked(make_app):  # noqa: F811
    app = make_app(user_lang="en")
    app.poll_traffic()
    app.daemon.allowed.insert(0, ("t1", "wlan0", "192.168.1.20", "192.168.1.5", "TCP", "22"))
    app.poll_traffic()
    assert app.sent == []  # off by default
    app.set_notifications("allowed", True)
    app.poll_traffic()  # baseline
    app.daemon.allowed.insert(0, ("t2", "wlan0", "192.168.1.21", "192.168.1.5", "TCP", "22"))
    app.poll_traffic()
    assert app.sent == [("allowed", "192.168.1.21 connected to port 22.")]
