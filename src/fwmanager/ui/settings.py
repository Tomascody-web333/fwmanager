"""Per-user interface settings in ``~/.config/fwmanager/settings.toml``.

The language chosen here wins over ``/etc/fwmanager/config.toml`` and the system locale.
Nothing here needs a password or touches the daemon.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from fwmanager.core.config import LANGUAGES


def settings_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "fwmanager" / "settings.toml"


SETUP_STAGES = ("welcome", "requirements", "profile", "done")


@dataclass(frozen=True)
class UserSettings:
    lang: str | None = None  # None: follow the system configuration
    notifications: bool = True
    setup_stage: str = "welcome"  # first-run wizard progress; "done" once finished or skipped

    def with_stage(self, stage: str) -> UserSettings:
        if stage not in SETUP_STAGES:
            raise ValueError("unknown setup stage")
        return replace(self, setup_stage=stage)

    def with_lang(self, lang: str | None) -> UserSettings:
        if lang is not None and lang not in LANGUAGES:
            raise ValueError("unsupported language")
        return replace(self, lang=lang)


def load(path: Path | None = None) -> UserSettings:
    """Missing or damaged files give the defaults; bad individual values are ignored."""
    path = path or settings_path()
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return UserSettings()
    lang = data.get("lang")
    notifications = data.get("notifications")
    stage = data.get("setup_stage")
    return UserSettings(
        lang if lang in LANGUAGES else None,
        notifications if isinstance(notifications, bool) else True,
        stage if stage in SETUP_STAGES else "welcome",
    )


def save(settings: UserSettings, path: Path | None = None) -> None:
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if settings.lang:
        lines.append(f'lang = "{settings.lang}"')
    lines.append(f"notifications = {'true' if settings.notifications else 'false'}")
    lines.append(f'setup_stage = "{settings.setup_stage}"')
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)
