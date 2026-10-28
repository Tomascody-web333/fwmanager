"""Recent blocked packets, read from ufw's log lines (``[UFW BLOCK] IN=... SRC=... DPT=...``).

The kernel writes these lines, but any local user can write a look-alike to syslog (``logger``),
and rsyslog files every line containing "[UFW " in /var/log/ufw.log. The kernel's own journal
cannot be written by users, so it is read first; the file is only a fallback, read from its end
so a huge log costs no more than a small one.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

UFW_LOG = Path("/var/log/ufw.log")
MAX_LINES = 4000
MAX_TAIL_BYTES = 1024 * 1024
JOURNALCTL = ("/usr/bin/journalctl", "/bin/journalctl")

_FIELD = re.compile(r"\b(IN|SRC|DST|PROTO|DPT)=(\S*)")
_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2}T[\d:.+-]+|[A-Z][a-z]{2}\s+\d+\s+[\d:]{8})")
# ufw's tags: BLOCK for refused packets, ALLOW for connections let in (logging medium and up).
TAGS = {"block": "UFW BLOCK", "allow": "UFW ALLOW"}
# What the kernel's lines look like in syslog: "host kernel: [ 1234.5] [UFW BLOCK] ...".
_FROM_KERNEL = {
    kind: re.compile(rf"(^|\s)kernel: (\[\s*\d+\.\d+\]\s*)?\[{tag}\]") for kind, tag in TAGS.items()
}
SAFE = re.compile(r"^[A-Za-z0-9_.:%/-]{0,64}$")


@dataclass(frozen=True)
class Blocked:
    when: str
    iface: str
    src: str
    dst: str
    proto: str
    dport: str


def parse_line(line: str, kind: str = "block") -> Blocked | None:
    if f"[{TAGS[kind]}]" not in line:
        return None
    fields = dict(_FIELD.findall(line))
    if not fields.get("SRC"):
        return None
    when = _TIME.match(line.strip())
    values = [fields.get(k, "") for k in ("IN", "SRC", "DST", "PROTO", "DPT")]
    if not all(SAFE.match(v) for v in values):
        return None  # a log line is attacker-influenced text: show only well-formed values
    return Blocked(when.group(1) if when else "", *values)


def parse_lines(
    text: str, limit: int, kernel_only: bool = False, kind: str = "block"
) -> list[Blocked]:
    """Newest first. ``kernel_only`` drops lines another program wrote (syslog files)."""
    out = []
    for line in reversed(text.splitlines()[-MAX_LINES:]):
        if kernel_only and not _FROM_KERNEL[kind].search(line):
            continue
        item = parse_line(line, kind)
        if item:
            out.append(item)
            if len(out) >= limit:
                break
    return out


def _tail(path: Path) -> str | None:
    try:
        with path.open("rb") as fh:
            size = fh.seek(0, os.SEEK_END)
            fh.seek(max(0, size - MAX_TAIL_BYTES))
            data = fh.read(MAX_TAIL_BYTES)
    except OSError:
        return None
    lines = data.decode(errors="replace").splitlines()
    if size > MAX_TAIL_BYTES:
        lines = lines[1:]  # the first one was cut in half
    return "\n".join(lines[-MAX_LINES:])


def _journal(run: Callable, kind: str = "block") -> str | None:
    """Kernel messages only (``-k``): nobody but the kernel can write those."""
    journalctl = next((p for p in JOURNALCTL if Path(p).exists()), None)
    if journalctl is None:
        return None
    try:
        proc = run(
            [journalctl, "-k", "-n", str(MAX_LINES), "--no-pager", "-o", "short-iso",
             "--grep", TAGS[kind]],
            capture_output=True, text=True, timeout=10, check=False,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def read_recent(
    limit: int = 50,
    log_path: Path = UFW_LOG,
    run: Callable = subprocess.run,
    kind: str = "block",
) -> list[Blocked]:
    """Prefer the kernel journal; fall back to /var/log/ufw.log where there is none.

    ``kind`` is ``block`` (refused packets) or ``allow`` (connections let in).
    """
    if kind not in TAGS:
        raise ValueError(kind)
    limit = max(1, min(int(limit), 500))
    found = parse_lines(_journal(run, kind) or "", limit, kernel_only=True, kind=kind)
    if found:
        return found
    return parse_lines(_tail(log_path) or "", limit, kernel_only=True, kind=kind)
