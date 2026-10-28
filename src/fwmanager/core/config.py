"""Language and system configuration.

Priority for the UI language: user settings > ``/etc/fwmanager/config.toml`` >
system locale (``pt*`` -> Portuguese, ``es*`` -> Spanish, ``fr*`` -> French, anything else ->
English).
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

SYSTEM_CONFIG = Path("/etc/fwmanager/config.toml")
LANGUAGES = ("pt", "en", "es", "fr")


def _read_lang(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        with Path(path).open("rb") as fh:
            lang = tomllib.load(fh).get("lang")
    except (OSError, ValueError):  # ValueError: bad TOML, or bytes that are not UTF-8
        return None
    return lang if lang in LANGUAGES else None


def system_language(env_lang: str | None = None) -> str:
    value = (env_lang if env_lang is not None else os.environ.get("LANG", "")).lower()
    return next((code for code in LANGUAGES if value.startswith(code)), "en")


def resolve_language(
    user_settings: Path | None = None,
    system_config: Path | None = SYSTEM_CONFIG,
    env_lang: str | None = None,
) -> str:
    return _read_lang(user_settings) or _read_lang(system_config) or system_language(env_lang)
