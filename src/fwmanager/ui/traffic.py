"""Turn ufw's log of blocked and allowed connections into a few calm notifications.

What was already in the log when the app opened is never announced. After that, new entries
are summed up in one notification per kind at most once a minute, so a scan of every port is
one message, not a thousand.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from fwmanager.i18n import N_, _

MIN_GAP = 60.0  # seconds between two notifications of the same kind

TITLES = {"blocked": N_("Connection attempts blocked"), "allowed": N_("Connections let in")}


def _key(item: tuple) -> tuple:
    when, iface, src, dst, proto, dport = item
    return when, iface, src, dst, proto, dport


def describe(kind: str, items: list[tuple]) -> str:
    """One sentence for a batch, newest entry named (items are newest first).

    Portuguese and English both only tell one from many, so two plain sentences do.
    """
    _when, _iface, src, _dst, proto, dport = items[0]
    port, n = dport or proto, len(items)
    if kind == "blocked":
        if n == 1:
            return _("{src} tried to reach port {port}.").format(src=src, port=port)
        return _("{n} attempts were blocked. The latest: {src} to port {port}.").format(
            n=n, src=src, port=port
        )
    if n == 1:
        return _("{src} connected to port {port}.").format(src=src, port=port)
    return _("{n} connections were let in. The latest: {src} to port {port}.").format(
        n=n, src=src, port=port
    )


class TrafficWatcher:
    def __init__(
        self, notify: Callable[[str, list], None], now: Callable[[], float] = time.monotonic
    ):
        self._notify, self._now = notify, now
        self._seen: dict[str, set] = {}  # kind -> entries already known (absent: no baseline yet)
        self._waiting: dict[str, list] = {"blocked": [], "allowed": []}
        self._last: dict[str, float] = {}

    def update(self, kind: str, items: list[tuple]) -> None:
        """Feed the newest entries of one kind (newest first), as read from the service."""
        keys = [_key(i) for i in items]
        if kind not in self._seen:  # first look: this is the baseline, announce nothing
            self._seen[kind] = set(keys)
            return
        new = [i for i, k in zip(items, keys, strict=True) if k not in self._seen[kind]]
        self._seen[kind].update(keys)
        self._waiting[kind] = new + self._waiting[kind]
        self._flush(kind)

    def _flush(self, kind: str) -> None:
        if not self._waiting[kind]:
            return
        now = self._now()
        if now - self._last.get(kind, -MIN_GAP) < MIN_GAP:
            return  # sent one a moment ago: these wait for the next
        self._notify(kind, self._waiting[kind])
        self._waiting[kind] = []
        self._last[kind] = now

    def forget(self, kind: str) -> None:
        """Switched off: the next time it is switched on starts from a fresh baseline."""
        self._seen.pop(kind, None)
        self._waiting[kind] = []
