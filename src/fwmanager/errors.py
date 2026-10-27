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
