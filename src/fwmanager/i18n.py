"""Minimal gettext setup. Strings shown to users go through ``_()``."""

from __future__ import annotations

import gettext
import os

from fwmanager import paths

DOMAIN = "fwmanager"

DEFAULT_LOCALEDIR = paths.LOCALEDIR

_translation: gettext.NullTranslations = gettext.NullTranslations()
_language = "en"


def current_language() -> str:
    return _language


# The catalogue each language is installed under, best first.
CATALOGUES = {"pt": ["pt_PT", "pt"], "es": ["es"], "fr": ["fr"], "en": ["en"]}


def set_language(lang: str | None, localedir: str | None = None) -> None:
    """Select the UI language ("pt", "pt_PT", "es_ES.UTF-8", ...). Unknown means English."""
    global _translation, _language
    lowered = (lang or "").lower()
    _language = next((code for code in CATALOGUES if lowered.startswith(code)), "en")
    if not lang:
        _translation = gettext.NullTranslations()
        return
    candidates = [lang.split(".")[0], *CATALOGUES[_language]]
    _translation = gettext.translation(
        DOMAIN,
        localedir=localedir or os.environ.get("FWMANAGER_LOCALEDIR") or DEFAULT_LOCALEDIR,
        languages=candidates,
        fallback=True,
    )


def _(message: str) -> str:
    return _translation.gettext(message)


def _n(singular: str, plural: str, n: int) -> str:
    """The form for ``n`` (1 attempt, 3 attempts); each language has its own rule."""
    return _translation.ngettext(singular, plural, n)


def N_(message: str) -> str:
    """Mark a string for translation without translating it now (see ``messages``)."""
    return message
