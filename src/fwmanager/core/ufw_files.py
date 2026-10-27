"""ufw's config files: the managed ping/mDNS block and exact snapshot/restore.

ufw's CLI cannot turn off ping or mDNS (its ``before.rules`` accepts them). We insert a small,
clearly delimited block right after the ``ctstate INVALID -j DROP`` anchor, ahead of those
accepts. The block header also records the active profile name.
"""

from __future__ import annotations

import errno
import os
import re
from dataclasses import dataclass
from pathlib import Path

from fwmanager.core.validate import validate_name
from fwmanager.errors import FwError, StoreError

BEGIN = "### BEGIN FWMANAGER"
END = "### END FWMANAGER ###"
BLOCK_RE = re.compile(
    rf"^{re.escape(BEGIN)} profile=([a-z][a-z0-9_-]{{0,31}}) ###\n.*?^{re.escape(END)}\n",
    re.M | re.S,
)
ANCHOR_V4 = "-A ufw-before-input -m conntrack --ctstate INVALID -j DROP"
ANCHOR_V6 = "-A ufw6-before-input -m conntrack --ctstate INVALID -j DROP"

SNAPSHOT_FILES = (
    "etc/ufw/ufw.conf",
    "etc/ufw/user.rules",
    "etc/ufw/user6.rules",
    "etc/ufw/before.rules",
    "etc/ufw/before6.rules",
    "etc/ufw/after.rules",
    "etc/ufw/after6.rules",
    "etc/default/ufw",
)


class UfwUnsupported(FwError):
    code = "ufw_unsupported"


def build_block(profile: str, ping: bool, mdns: bool, v6: bool) -> str:
    lines = [
        f"{BEGIN} profile={validate_name(profile, 'profile')} ###",
        "# Managed by fwmanager. Do not edit; it is rewritten on every profile change.",
    ]
    chain = "ufw6-before-input" if v6 else "ufw-before-input"
    if not ping:
        icmp = "-p icmpv6 --icmpv6-type echo-request" if v6 else "-p icmp --icmp-type echo-request"
        lines.append(f"-A {chain} {icmp} -j DROP")
    if not mdns:
        dest = "ff02::fb" if v6 else "224.0.0.251"
        lines.append(f"-A {chain} -p udp -d {dest} --dport 5353 -j DROP")
    lines.append(END)
    return "\n".join(lines) + "\n"


def _our_lines() -> frozenset[str]:
    """Every line a block of ours can hold besides its BEGIN and END markers."""
    lines = set()
    for v6 in (False, True):
        lines.update(build_block("x", False, False, v6).splitlines()[1:-1])
    return frozenset(lines)


def strip_block(text: str) -> str:
    """Remove our block, including what is left of one whose BEGIN or END was deleted by hand.

    Leftovers are only our marker lines and the lines of ours right next to them, so a rule the
    administrator wrote elsewhere is kept even if it looks the same.
    """
    text = BLOCK_RE.sub("", text)
    if BEGIN not in text and END not in text:
        return text
    ours = _our_lines()
    lines = text.splitlines(keepends=True)
    drop = set()
    for i, line in enumerate(lines):
        if line.startswith(BEGIN):
            drop.add(i)
            j = i + 1
            while j < len(lines) and lines[j].rstrip("\n") in ours:
                drop.add(j)
                j += 1
        elif line.rstrip("\n") == END:
            drop.add(i)
            j = i - 1
            while j >= 0 and lines[j].rstrip("\n") in ours:
                drop.add(j)
                j -= 1
    return "".join(line for i, line in enumerate(lines) if i not in drop)


def read_profile_marker(text: str) -> str | None:
    m = BLOCK_RE.search(text)
    return m.group(1) if m else None


def insert_block(text: str, block: str, anchor: str) -> str:
    """Replace any previous block and place ``block`` right after the anchor line."""
    text = strip_block(text)
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.strip() == anchor:
            lines.insert(i + 1, block)
            return "".join(lines)
    raise UfwUnsupported("unrecognised ufw before.rules (anchor line missing)")


def write_atomic(path: Path, data: bytes, mode: int | None = None) -> None:
    """Replace ``path`` keeping owner and permissions; a crash never leaves a partial file.

    The service may write only the file itself, not its directory (systemd's ReadWritePaths
    names /etc/default/ufw alone). Then it is rewritten in place, as ufw itself does.
    """
    tmp = path.with_name(f".{path.name}.fwmanager-tmp")
    try:
        st = path.stat() if path.exists() else None
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        except OSError as exc:
            if st is None or exc.errno not in (errno.EROFS, errno.EACCES, errno.EPERM):
                raise
            _write_in_place(path, data)
            return
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if st is not None:
            os.chmod(tmp, st.st_mode & 0o7777)
            os.chown(tmp, st.st_uid, st.st_gid)
        elif mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except OSError as exc:
        raise StoreError(f"cannot write {path.name}: {exc.strerror}") from exc


def _write_in_place(path: Path, data: bytes) -> None:
    with open(path, "r+b") as fh:
        fh.write(data)
        fh.truncate()
        fh.flush()
        os.fsync(fh.fileno())


def apply_block(config_dir: Path, profile: str, ping: bool, mdns: bool, v6: bool) -> bool:
    """Rewrite before.rules (and before6.rules when IPv6 is on) with the managed block.

    Returns True if anything changed, i.e. ufw must reload to pick it up.
    """
    targets = [("before.rules", ANCHOR_V4, False)]
    if v6:
        targets.append(("before6.rules", ANCHOR_V6, True))
    # Compute everything first so a missing anchor changes nothing at all.
    updated = []
    for name, anchor, is_v6 in targets:
        path = config_dir / name
        try:
            text = path.read_text()
        except OSError as exc:
            raise UfwUnsupported(f"cannot read {name}") from exc
        new = insert_block(text, build_block(profile, ping, mdns, is_v6), anchor)
        if new != text:
            updated.append((path, new))
    for path, text in updated:
        write_atomic(path, text.encode())
    return bool(updated)


def remove_blocks(config_dir: Path) -> None:
    for name in ("before.rules", "before6.rules"):
        path = config_dir / name
        try:
            text = path.read_text()
        except OSError:
            continue
        stripped = strip_block(text)
        if stripped != text:
            write_atomic(path, stripped.encode())


def active_marker(config_dir: Path) -> str | None:
    try:
        return read_profile_marker((config_dir / "before.rules").read_text())
    except OSError:
        return None


# -- exact snapshot ---


@dataclass(frozen=True)
class FileState:
    data: bytes
    mode: int


@dataclass(frozen=True)
class FilesSnapshot:
    files: dict[str, FileState | None]  # relative path -> content (None: did not exist)
    was_active: bool


def take_files(root: Path) -> dict[str, FileState | None]:
    out: dict[str, FileState | None] = {}
    for rel in SNAPSHOT_FILES:
        path = root / rel
        try:
            out[rel] = FileState(path.read_bytes(), path.stat().st_mode & 0o7777)
        except FileNotFoundError:
            out[rel] = None
        except OSError as exc:
            raise StoreError(f"cannot read {rel}: {exc.strerror}") from exc
    return out


def restore_files(root: Path, files: dict[str, FileState | None]) -> None:
    for rel, state in files.items():
        path = root / rel
        if state is None:
            path.unlink(missing_ok=True)
        else:
            write_atomic(path, state.data, state.mode)
