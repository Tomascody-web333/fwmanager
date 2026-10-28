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
# What may send a desktop notification. "reverted" is the original `notifications` switch.
NOTIFY_KINDS = ("reverted", "switched", "blocked", "allowed")
_NOTIFY_KEYS = {
    "switched": "notify_switched",
    "blocked": "notify_blocked",
    "allowed": "notify_allowed",
}


@dataclass(frozen=True)
class UserSettings:
    lang: str | None = None  # None: follow the system configuration
    notifications: bool = True  # a change was undone automatically
    setup_stage: str = "welcome"  # first-run wizard progress; "done" once finished or skipped
    notify_switched: bool = True  # the profile changed by itself (a network came up)
    notify_blocked: bool = False  # connection attempts were blocked (needs logging on)
    notify_allowed: bool = False  # connections were let in (needs logging medium or high)

    def wants(self, kind: str) -> bool:
        if kind == "reverted":
            return self.notifications
        return getattr(self, _NOTIFY_KEYS[kind])

    def with_notify(self, kind: str, enabled: bool) -> UserSettings:
        if kind == "reverted":
            return replace(self, notifications=enabled)
        return replace(self, **{_NOTIFY_KEYS[kind]: enabled})

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
    except (OSError, ValueError):  # ValueError: bad TOML, or bytes that are not UTF-8
        return UserSettings()
    lang = data.get("lang")
    notifications = data.get("notifications")
    stage = data.get("setup_stage")
    defaults = UserSettings()
    flags = {
        key: data[key] if isinstance(data.get(key), bool) else getattr(defaults, key)
        for key in _NOTIFY_KEYS.values()
    }
    return UserSettings(
        lang if lang in LANGUAGES else None,
        notifications if isinstance(notifications, bool) else True,
        stage if stage in SETUP_STAGES else "welcome",
        **flags,
    )


def save(settings: UserSettings, path: Path | None = None) -> None:
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if settings.lang:
        lines.append(f'lang = "{settings.lang}"')
    lines.append(f"notifications = {'true' if settings.notifications else 'false'}")
    lines.append(f'setup_stage = "{settings.setup_stage}"')
    for key in _NOTIFY_KEYS.values():
        lines.append(f"{key} = {'true' if getattr(settings, key) else 'false'}")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)
