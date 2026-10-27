"""One-shot timer abstraction; the production implementation uses the GLib main loop."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class Scheduler(Protocol):
    def call_later(self, seconds: int, callback: Callable[[], None]) -> object: ...

    def cancel(self, handle: object) -> None: ...


class GLibScheduler:
    """Uses ``GLib.timeout_add_seconds`` (never ``threading.Timer``)."""

    def __init__(self) -> None:
        from gi.repository import GLib

        self._glib = GLib

    def call_later(self, seconds: int, callback: Callable[[], None]) -> int:
        def _fire() -> bool:
            callback()
            return False  # one-shot

        return self._glib.timeout_add_seconds(seconds, _fire)

    def cancel(self, handle: object) -> None:
        self._glib.source_remove(handle)
