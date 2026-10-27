"""Run a blocking call off the GTK main loop and hand the result back on it."""

from __future__ import annotations

import threading
from collections.abc import Callable

from gi.repository import GLib

from fwmanager.errors import FwError


def run_async(fn: Callable[[], object], on_done: Callable[[object, FwError | None], None]) -> None:
    """``fn`` runs in a thread; ``on_done(result, error)`` runs in the main loop."""

    def work() -> None:
        result, error = None, None
        try:
            result = fn()
        except FwError as exc:
            error = exc
        except Exception as exc:  # never let a thread die silently
            error = FwError(str(exc), code="internal_error")

        def deliver() -> bool:
            on_done(result, error)
            return False

        GLib.idle_add(deliver)

    threading.Thread(target=work, daemon=True).start()


def run_sync(fn: Callable[[], object], on_done: Callable[[object, FwError | None], None]) -> None:
    """Same contract, immediately; for tests."""
    try:
        on_done(fn(), None)
    except FwError as exc:
        on_done(None, exc)
