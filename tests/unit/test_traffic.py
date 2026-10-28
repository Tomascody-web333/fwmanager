from fwmanager.i18n import set_language
from fwmanager.ui import settings as us
from fwmanager.ui import traffic

A = ("t1", "wlan0", "203.0.113.9", "192.168.1.5", "TCP", "22")
B = ("t2", "wlan0", "198.51.100.7", "192.168.1.5", "TCP", "445")
C = ("t3", "wlan0", "198.51.100.8", "192.168.1.5", "UDP", "")


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def watcher():
    sent, clock = [], Clock()
    return (
        traffic.TrafficWatcher(lambda kind, items: sent.append((kind, items)), clock),
        sent,
        clock,
    )


def test_what_was_in_the_log_before_is_never_announced():
    w, sent, _clock = watcher()
    w.update("blocked", [A])
    assert sent == []


def test_new_entries_are_announced_together():
    w, sent, _clock = watcher()
    w.update("blocked", [A])
    w.update("blocked", [C, B, A])
    assert sent == [("blocked", [C, B])]


def test_at_most_one_notification_a_minute_and_nothing_is_lost():
    w, sent, clock = watcher()
    w.update("blocked", [])
    w.update("blocked", [A])
    clock.t += 10
    w.update("blocked", [B, A])  # too soon: waits
    assert sent == [("blocked", [A])]
    clock.t += traffic.MIN_GAP
    w.update("blocked", [B, A])  # nothing new, but the waiting one goes out now
    assert sent == [("blocked", [A]), ("blocked", [B])]


def test_kinds_are_independent_and_forgetting_resets_the_baseline():
    w, sent, _clock = watcher()
    w.update("blocked", [])
    w.update("allowed", [])
    w.update("allowed", [B])
    w.update("blocked", [A])
    assert [k for k, _ in sent] == ["allowed", "blocked"]
    w.forget("blocked")
    w.update("blocked", [C, A])  # a fresh baseline: not announced
    assert len(sent) == 2


def test_describe_in_plain_words():
    set_language(None)
    assert traffic.describe("blocked", [A]) == "203.0.113.9 tried to reach port 22."
    assert traffic.describe("blocked", [B, A]) == (
        "2 attempts were blocked. The latest: 198.51.100.7 to port 445."
    )
    assert traffic.describe("allowed", [C]) == "198.51.100.8 connected to port UDP."
    assert traffic.describe("allowed", [B, A]).startswith("2 connections were let in.")


def test_notification_choices_are_saved_and_old_files_still_load(tmp_path):
    path = tmp_path / "settings.toml"
    s = us.UserSettings().with_notify("blocked", True).with_notify("switched", False)
    us.save(s, path)
    back = us.load(path)
    assert back.wants("blocked") and not back.wants("switched") and back.wants("reverted")
    path.write_text("notifications = false\n")  # written by an older version
    old = us.load(path)
    assert not old.wants("reverted") and old.wants("switched") and not old.wants("allowed")
