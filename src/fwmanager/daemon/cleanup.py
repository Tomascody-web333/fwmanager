"""Uninstall-time cleanup: remove everything fwmanager added to ufw, leaving the machine open.

Only what we own is touched: our commented rules and our managed block. If fwmanager was the
one that switched ufw on, it is switched off again so the machine is not left filtering with
nobody managing it. A ufw the user enabled themselves stays enabled.
"""

from __future__ import annotations

import logging

from fwmanager.core.backend import UfwBackend
from fwmanager.core.store import Store

log = logging.getLogger("fwmanager")


def cleanup(backend: UfwBackend, store: Store) -> str:
    """Returns what was done: ``"disabled"``, ``"cleaned"``."""
    backend.remove_everything()
    original = store.load_original_defaults()
    if original:
        restored = backend.restore_defaults(original)
        if restored:
            log.info("restored the original ufw defaults: %s", ", ".join(restored))
        store.clear_original_defaults()
    if store.ufw_enabled_by_us():
        if backend.is_active():
            backend.ufw.run(["--force", "disable"])
        store.clear_ufw_marker()
        log.info("ufw was enabled by fwmanager: disabled it again")
        return "disabled"
    return "cleaned"
