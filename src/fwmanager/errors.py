"""Error types carrying stable machine-readable codes.

The daemon logs the English ``message``; the UI/CLI translate the ``code``.
"""

from __future__ import annotations


class FwError(Exception):
    """Base class for all fwmanager errors."""

    code = "internal_error"

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message or (code or self.code))
        if code is not None:
            self.code = code
        self.message = message or self.code


class ValidationError(FwError):
    code = "invalid_input"


class NftError(FwError):
    code = "nft_failed"


class PendingError(FwError):
    code = "apply_pending"


class StoreError(FwError):
    code = "store_error"


class StoreCorrupt(StoreError):
    code = "state_corrupt"


class AccessDenied(FwError):
    code = "access_denied"


DBUS_ERROR_PREFIX = "org.fwmanager.Error."


def dbus_error_name(code: str) -> str:
    """``invalid_profile`` -> ``org.fwmanager.Error.InvalidProfile``."""
    return DBUS_ERROR_PREFIX + "".join(part.capitalize() for part in code.split("_"))


def code_from_dbus_error(name: str) -> str:
    """Inverse of :func:`dbus_error_name`; unknown names map to ``internal_error``."""
    if not name.startswith(DBUS_ERROR_PREFIX):
        return "internal_error"
    camel = name[len(DBUS_ERROR_PREFIX) :]
    out = ""
    for ch in camel:
        out += ("_" + ch.lower()) if ch.isupper() and out else ch.lower()
    return out
