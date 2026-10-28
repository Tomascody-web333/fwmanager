"""The application: language handling, notifications and window life cycle."""

from __future__ import annotations

from dataclasses import replace

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio  # noqa: E402

from fwmanager.core.config import SYSTEM_CONFIG, resolve_language  # noqa: E402
from fwmanager.i18n import _, set_language  # noqa: E402
from fwmanager.ui import APP_ID, state  # noqa: E402
from fwmanager.ui import settings as us  # noqa: E402
from fwmanager.ui.controller import Controller  # noqa: E402
from fwmanager.ui.window import MainWindow  # noqa: E402


class FwApplication(Adw.Application):
    def __init__(
        self, controller_factory=Controller, settings_path=None, system_config=SYSTEM_CONFIG
    ):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self._factory = controller_factory
        self._settings_path = settings_path
        self._system_config = system_config
        self.controller: Controller | None = None
        self.window: MainWindow | None = None
        self.user_settings = us.UserSettings()
        self._wizard_options: dict = {}  # tests inject fakes for the helper and the relaunch

    # -- language ---

    def _apply_language(self) -> None:
        self.user_settings = us.load(self._settings_path)
        path = self._settings_path or us.settings_path()
        set_language(resolve_language(path, self._system_config))

    def change_language(self, code: str) -> None:
        """Save the choice for this user and rebuild the window. No password, no daemon."""
        if code == (self.user_settings.lang or ""):
            return
        us.save(self.user_settings.with_lang(code), self._settings_path)
        self._apply_language()
        self._build_window()

    def set_setup_stage(self, stage: str) -> None:
        """Remember how far first-run setup got (it resumes there after a restart)."""
        self.user_settings = self.user_settings.with_stage(stage)
        us.save(self.user_settings, self._settings_path)

    def set_notifications(self, enabled: bool) -> None:
        self.user_settings = replace(self.user_settings, notifications=enabled)
        us.save(self.user_settings, self._settings_path)

    # -- life cycle ---

    def do_activate(self) -> None:
        if self.window is not None:
            self.window.present()
            return
        self._apply_language()
        self.controller = self._factory()
        self.controller.signal_listeners.append(self._on_daemon_signal)
        self._build_window()

    def _build_window(self) -> None:
        old = self.window
        self.window = MainWindow(
            self,
            self.controller,
            self.user_settings,
            self.change_language,
            self.set_notifications,
            self.set_setup_stage,
            self._wizard_options,
        )
        self.window.present()
        if old is not None:
            old.destroy()

    def _on_daemon_signal(self, name: str, args: tuple) -> None:
        if name == "Reverted" and self.user_settings.notifications:
            note = Gio.Notification.new(_("fwmanager"))
            note.set_body(
                _("{profile} was undone because it was not confirmed in time.").format(
                    profile=state.profile_title(args[0])
                )
            )
            self.send_notification("reverted", note)
