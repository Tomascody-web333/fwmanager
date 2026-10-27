"""Minimal gettext setup. Strings shown to users go through ``_()``."""

from __future__ import annotations

import gettext
import os

DOMAIN = "fwmanager"

_translation: gettext.NullTranslations = gettext.NullTranslations()


def set_language(lang: str | None, localedir: str | None = None) -> None:
    """Select the UI language ("pt", "pt_PT", "en", ...). Unknown means English."""
    global _translation
    if not lang:
        _translation = gettext.NullTranslations()
        return
    lang = lang.split(".")[0]
    candidates = [lang]
    if lang.lower().startswith("pt"):
        candidates = ["pt_PT", "pt"]
    _translation = gettext.translation(
        DOMAIN,
        localedir=localedir or os.environ.get("FWMANAGER_LOCALEDIR"),
        languages=candidates,
        fallback=True,
    )


def _(message: str) -> str:
    return _translation.gettext(message)


def N_(message: str) -> str:
    """Mark a string for translation without translating it now (see ``messages``)."""
    return message
