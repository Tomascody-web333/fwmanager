"""Recent blocked packets, read from ufw's log lines (``[UFW BLOCK] IN=... SRC=... DPT=...``)."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

UFW_LOG = Path("/var/log/ufw.log")
MAX_LINES = 4000
JOURNALCTL = ("/usr/bin/journalctl", "/bin/journalctl")

_FIELD = re.compile(r"\b(IN|SRC|DST|PROTO|DPT)=(\S*)")
_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2}T[\d:.+-]+|[A-Z][a-z]{2}\s+\d+\s+[\d:]{8})")
SAFE = re.compile(r"^[A-Za-z0-9_.:%/-]{0,64}$")


@dataclass(frozen=True)
class Blocked:
    when: str
    iface: str
    src: str
    dst: str
    proto: str
    dport: str


def parse_line(line: str) -> Blocked | None:
    if "[UFW BLOCK]" not in line:
        return None
    fields = dict(_FIELD.findall(line))
    if not fields.get("SRC"):
        return None
    when = _TIME.match(line.strip())
    values = [fields.get(k, "") for k in ("IN", "SRC", "DST", "PROTO", "DPT")]
    if not all(SAFE.match(v) for v in values):
        return None  # a log line is attacker-influenced text: show only well-formed values
    return Blocked(when.group(1) if when else "", *values)


def parse_lines(text: str, limit: int) -> list[Blocked]:
    """Newest first."""
    out = []
    for line in reversed(text.splitlines()[-MAX_LINES:]):
        item = parse_line(line)
        if item:
            out.append(item)
            if len(out) >= limit:
                break
    return out


def _tail(path: Path) -> str | None:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-MAX_LINES:])
    except OSError:
        return None


def read_recent(
    limit: int = 50,
    log_path: Path = UFW_LOG,
    run: Callable = subprocess.run,
) -> list[Blocked]:
    """Prefer /var/log/ufw.log; fall back to the kernel journal on journald-only systems."""
    limit = max(1, min(int(limit), 500))
    text = _tail(log_path)
    if text is None:
        journalctl = next((p for p in JOURNALCTL if Path(p).exists()), None)
        if journalctl is None:
            return []
        try:
            proc = run(
                [journalctl, "-k", "-n", str(MAX_LINES), "--no-pager", "-o", "short-iso",
                 "--grep", "UFW BLOCK"],
                capture_output=True, text=True, timeout=10, check=False,
            )  # fmt: skip
        except (OSError, subprocess.TimeoutExpired):
            return []
        text = proc.stdout if proc.returncode == 0 else ""
    return parse_lines(text, limit)
