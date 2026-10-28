import subprocess

import pytest

from fwmanager.core import blocklog as bl

LINE = (
    "2026-10-27T17:32:01.123456+00:00 host kernel: [UFW BLOCK] IN=wlan0 OUT= "
    "MAC=aa:bb SRC=203.0.113.9 DST=192.168.1.5 LEN=60 PROTO=TCP SPT=51515 DPT=22 WINDOW=64240"
)
OLD = (
    "Oct 27 17:32:01 host kernel: [UFW BLOCK] IN=eth0 OUT= "
    "SRC=10.0.0.7 DST=10.0.0.2 PROTO=UDP DPT=5353"
)


def test_parse_iso_and_syslog_lines():
    b = bl.parse_line(LINE)
    assert b == bl.Blocked(
        "2026-10-27T17:32:01.123456+00:00", "wlan0", "203.0.113.9", "192.168.1.5", "TCP", "22"
    )
    o = bl.parse_line(OLD)
    assert o.when == "Oct 27 17:32:01" and o.proto == "UDP" and o.dport == "5353"


def test_ipv6_and_missing_port():
    b = bl.parse_line("[UFW BLOCK] IN=eth0 SRC=fe80::1 DST=ff02::fb PROTO=ICMPV6")
    assert b.src == "fe80::1" and b.dport == "" and b.when == ""


def test_other_lines_are_ignored():
    assert bl.parse_line("[UFW ALLOW] IN=eth0 SRC=1.2.3.4") is None
    assert bl.parse_line("kernel: something else") is None
    assert bl.parse_line("[UFW BLOCK] IN=eth0 OUT=") is None  # no SRC


def test_log_text_cannot_inject_odd_values():
    # The log is text anyone on the network can influence; only well-formed values pass.
    assert bl.parse_line("[UFW BLOCK] IN=eth0 SRC=1.2.3.4<script> DST=1.1.1.1 PROTO=TCP") is None
    assert bl.parse_line("[UFW BLOCK] IN=eth0 SRC=" + "9" * 200 + " DST=1.1.1.1") is None


def test_newest_first_and_limit():
    text = "\n".join(
        f"[UFW BLOCK] IN=eth0 SRC=10.0.0.{i} DST=1.1.1.1 PROTO=TCP DPT=22" for i in range(10)
    )
    got = bl.parse_lines(text, 3)
    assert [b.src for b in got] == ["10.0.0.9", "10.0.0.8", "10.0.0.7"]


@pytest.fixture
def no_journal(monkeypatch):
    monkeypatch.setattr(bl, "JOURNALCTL", ("/nonexistent/journalctl",))


def test_read_recent_uses_the_ufw_log_without_a_journal(tmp_path, no_journal):
    log = tmp_path / "ufw.log"
    log.write_text(LINE + "\n")
    assert bl.read_recent(10, log)[0].src == "203.0.113.9"


def test_lines_a_user_wrote_to_syslog_are_not_shown(tmp_path, no_journal):
    # `logger "[UFW BLOCK] ..."` lands in ufw.log under the user's name, not the kernel's.
    log = tmp_path / "ufw.log"
    fake = "2026-10-27T17:40:00+00:00 host mallory: [UFW BLOCK] IN=wlan0 SRC=6.6.6.6 DST=1.1.1.1"
    log.write_text(LINE + "\n" + fake + "\n")
    assert [b.src for b in bl.read_recent(10, log)] == ["203.0.113.9"]
    with_uptime = "Oct 27 17:32:01 host kernel: [ 1234.567890] [UFW BLOCK] IN=eth0 SRC=10.0.0.7"
    log.write_text(with_uptime + "\n")
    assert bl.read_recent(10, log)[0].src == "10.0.0.7"


def test_a_huge_log_is_read_from_its_end_only(tmp_path, no_journal, monkeypatch):
    monkeypatch.setattr(bl, "MAX_TAIL_BYTES", 4096)
    log = tmp_path / "ufw.log"
    old = LINE.replace("203.0.113.9", "198.51.100.1")
    log.write_text((old + "\n") * 2000 + LINE + "\n")
    got = bl.read_recent(500, log)
    assert got[0].src == "203.0.113.9" and len(got) < 4096 // len(LINE) + 1


def test_the_kernel_journal_comes_first(tmp_path, monkeypatch):
    monkeypatch.setattr(bl, "JOURNALCTL", (str(tmp_path),))
    log = tmp_path / "ufw.log"
    log.write_text(LINE + "\n")
    got = bl.read_recent(5, log, lambda argv, **kw: subprocess.CompletedProcess(argv, 0, OLD, ""))
    assert [b.src for b in got] == ["10.0.0.7"]


def test_read_recent_falls_back_to_journal(tmp_path, monkeypatch):
    monkeypatch.setattr(bl, "JOURNALCTL", (str(tmp_path),))  # any existing path stands in
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, OLD + "\n", "")

    got = bl.read_recent(5, tmp_path / "missing.log", run)
    assert got[0].iface == "eth0"
    assert "--grep" in calls[0] and calls[0][1] == "-k"  # fixed argument list


def test_read_recent_degrades_quietly(tmp_path, monkeypatch):
    monkeypatch.setattr(bl, "JOURNALCTL", ("/nonexistent/journalctl",))
    assert bl.read_recent(5, tmp_path / "missing.log") == []
    monkeypatch.setattr(bl, "JOURNALCTL", (str(tmp_path),))

    def boom(argv, **kw):
        raise OSError("no")

    assert bl.read_recent(5, tmp_path / "missing.log", boom) == []


def test_limit_is_clamped(tmp_path, no_journal):
    log = tmp_path / "ufw.log"
    log.write_text("\n".join(LINE for _ in range(700)) + "\n")
    assert len(bl.read_recent(10_000, log)) == 500
    assert len(bl.read_recent(0, log)) == 1


ALLOW = (
    "2026-10-27T17:40:01+00:00 host kernel: [UFW ALLOW] IN=wlan0 OUT= SRC=192.168.1.20 "
    "DST=192.168.1.5 PROTO=TCP SPT=40000 DPT=22"
)


def test_allowed_connections_are_read_separately(tmp_path, no_journal):
    log = tmp_path / "ufw.log"
    log.write_text(LINE + "\n" + ALLOW + "\n")
    assert [b.src for b in bl.read_recent(10, log, kind="allow")] == ["192.168.1.20"]
    assert [b.src for b in bl.read_recent(10, log)] == ["203.0.113.9"]  # block by default
    fake = ALLOW.replace("kernel:", "mallory:")
    log.write_text(fake + "\n")
    assert bl.read_recent(10, log, kind="allow") == []  # same protection as for BLOCK


def test_the_journal_is_asked_for_the_right_tag(tmp_path, monkeypatch):
    monkeypatch.setattr(bl, "JOURNALCTL", (str(tmp_path),))
    seen = []

    def run(argv, **kw):
        seen.append(argv[-1])
        return subprocess.CompletedProcess(argv, 0, ALLOW, "")

    assert bl.read_recent(5, tmp_path / "x", run, kind="allow")[0].dport == "22"
    assert seen == ["UFW ALLOW"]
    with pytest.raises(ValueError):
        bl.read_recent(5, tmp_path / "x", run, kind="all")
