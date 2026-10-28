import os
import stat

import pytest

from fwmanager.core import ufw_files as uf
from fwmanager.errors import FwError

V4 = """*filter
:ufw-before-input - [0:0]
-A ufw-before-input -i lo -j ACCEPT
-A ufw-before-input -m conntrack --ctstate INVALID -j DROP
-A ufw-before-input -p icmp --icmp-type echo-request -j ACCEPT
COMMIT
"""
V6 = V4.replace("ufw-before-input", "ufw6-before-input")


def test_block_contents():
    assert uf.build_block("public", ping=False, mdns=False, v6=False) == (
        "### BEGIN FWMANAGER profile=public ###\n"
        "# Managed by fwmanager. Do not edit; it is rewritten on every profile change.\n"
        "-A ufw-before-input -p icmp --icmp-type echo-request -j DROP\n"
        "-A ufw-before-input -p udp -d 224.0.0.251 --dport 5353 -j DROP\n"
        "### END FWMANAGER ###\n"
    )
    b6 = uf.build_block("public", False, False, v6=True)
    assert "icmpv6 --icmpv6-type echo-request -j DROP" in b6 and "ff02::fb" in b6
    allowed = uf.build_block("home", True, True, False)
    assert "DROP" not in allowed and "profile=home" in allowed


def test_insert_goes_right_after_the_anchor_and_before_the_accepts():
    out = uf.insert_block(V4, uf.build_block("public", False, True, False), uf.ANCHOR_V4)
    lines = out.splitlines()
    i = lines.index(uf.ANCHOR_V4)
    assert lines[i + 1].startswith("### BEGIN FWMANAGER")
    assert out.index("echo-request -j DROP") < out.index("echo-request -j ACCEPT")


def test_insert_is_idempotent_and_replaces_the_old_block():
    b1 = uf.build_block("public", False, False, False)
    b2 = uf.build_block("home", True, True, False)
    once = uf.insert_block(V4, b1, uf.ANCHOR_V4)
    twice = uf.insert_block(once, b1, uf.ANCHOR_V4)
    assert once == twice
    swapped = uf.insert_block(once, b2, uf.ANCHOR_V4)
    assert swapped.count("BEGIN FWMANAGER") == 1 and "profile=home" in swapped
    assert uf.read_profile_marker(swapped) == "home"


def test_strip_restores_the_original_exactly():
    out = uf.insert_block(V4, uf.build_block("x", False, False, False), uf.ANCHOR_V4)
    assert uf.strip_block(out) == V4


@pytest.mark.parametrize("cut", [uf.END, "### BEGIN FWMANAGER profile=public ###"])
def test_a_block_cut_by_hand_is_cleaned_up_whole(cut):
    old = uf.insert_block(V4, uf.build_block("public", False, False, False), uf.ANCHOR_V4)
    damaged = old.replace(cut + "\n", "")
    again = uf.insert_block(damaged, uf.build_block("home", True, True, False), uf.ANCHOR_V4)
    assert "echo-request -j DROP" not in again and "5353 -j DROP" not in again
    assert again.count("FWMANAGER") == 2 and uf.read_profile_marker(again) == "home"
    assert uf.strip_block(again) == V4


def test_an_admins_identical_rule_elsewhere_survives_the_cleanup():
    mine = "-A ufw-before-input -p icmp --icmp-type echo-request -j DROP\n"
    text = V4.replace("COMMIT\n", mine + "COMMIT\n")
    out = uf.insert_block(text, uf.build_block("public", False, False, False), uf.ANCHOR_V4)
    damaged = out.replace(uf.END + "\n", "")
    assert uf.strip_block(damaged) == text  # theirs, before COMMIT, is not next to our marker


def test_missing_anchor_is_unsupported():
    with pytest.raises(uf.UfwUnsupported):
        uf.insert_block("*filter\nCOMMIT\n", "x\n", uf.ANCHOR_V4)


def test_apply_block_changes_nothing_if_any_file_is_unrecognised(tmp_path):
    (tmp_path / "before.rules").write_text(V4)
    (tmp_path / "before6.rules").write_text("*filter\nCOMMIT\n")  # no anchor
    with pytest.raises(uf.UfwUnsupported):
        uf.apply_block(tmp_path, "public", False, False, v6=True)
    assert (tmp_path / "before.rules").read_text() == V4  # untouched: all or nothing


def test_apply_and_remove_blocks(tmp_path):
    (tmp_path / "before.rules").write_text(V4)
    (tmp_path / "before6.rules").write_text(V6)
    uf.apply_block(tmp_path, "public", False, False, v6=True)
    assert uf.active_marker(tmp_path) == "public"
    assert "ufw6-before-input -p icmpv6" in (tmp_path / "before6.rules").read_text()
    uf.remove_blocks(tmp_path)
    assert (tmp_path / "before.rules").read_text() == V4
    assert (tmp_path / "before6.rules").read_text() == V6
    assert uf.active_marker(tmp_path) is None


def test_v6_file_untouched_when_ipv6_is_off(tmp_path):
    (tmp_path / "before.rules").write_text(V4)
    (tmp_path / "before6.rules").write_text(V6)
    uf.apply_block(tmp_path, "public", False, False, v6=False)
    assert (tmp_path / "before6.rules").read_text() == V6


def test_write_atomic_keeps_mode_and_leaves_no_temp(tmp_path):
    p = tmp_path / "before.rules"
    p.write_text("old")
    os.chmod(p, 0o640)
    uf.write_atomic(p, b"new")
    assert p.read_text() == "new" and stat.S_IMODE(p.stat().st_mode) == 0o640
    assert [x.name for x in tmp_path.iterdir()] == ["before.rules"]


def test_write_atomic_failure_is_an_error(tmp_path):
    with pytest.raises(FwError):
        uf.write_atomic(tmp_path / "missing-dir" / "f", b"x")


def test_snapshot_roundtrip_is_byte_exact(tmp_path):
    for rel in uf.SNAPSHOT_FILES:
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(f"content of {rel}\n".encode())
        os.chmod(f, 0o640)
    snap = uf.take_files(tmp_path)
    (tmp_path / "etc/ufw/user.rules").write_text("changed")
    (tmp_path / "etc/ufw/before.rules").unlink()
    uf.restore_files(tmp_path, snap)
    assert (tmp_path / "etc/ufw/user.rules").read_text() == "content of etc/ufw/user.rules\n"
    assert (tmp_path / "etc/ufw/before.rules").exists()
    assert stat.S_IMODE((tmp_path / "etc/ufw/before.rules").stat().st_mode) == 0o640


def test_snapshot_of_missing_file_restores_to_missing(tmp_path):
    (tmp_path / "etc/ufw").mkdir(parents=True)
    snap = uf.take_files(tmp_path)
    assert all(v is None for v in snap.values())
    (tmp_path / "etc/ufw/user.rules").write_text("created later")
    uf.restore_files(tmp_path, snap)
    assert not (tmp_path / "etc/ufw/user.rules").exists()
