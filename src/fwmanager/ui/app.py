"""The application: language handling, notifications and window life cycle."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib  # noqa: E402

from fwmanager.core.config import SYSTEM_CONFIG, resolve_language  # noqa: E402
from fwmanager.i18n import _, set_language  # noqa: E402
from fwmanager.ui import APP_ID, state, traffic  # noqa: E402
from fwmanager.ui import settings as us  # noqa: E402
from fwmanager.ui.controller import Controller  # noqa: E402
from fwmanager.ui.window import MainWindow  # noqa: E402

TRAFFIC_POLL_SECONDS = 20


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
        self.traffic = traffic.TrafficWatcher(self._traffic_notification)
        self.sent: list[tuple[str, str]] = []
        self._uninstall_options: dict = {}

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

    def set_notifications(self, kind: str, enabled: bool) -> None:
        """One kind of notification on or off (``reverted``, ``switched``, ``blocked``...)."""
        self.user_settings = self.user_settings.with_notify(kind, enabled)
        us.save(self.user_settings, self._settings_path)
        if not enabled and kind in traffic.TITLES:
            self.traffic.forget(kind)

    # -- life cycle ---

    def do_activate(self) -> None:
        if self.window is not None:
            self.window.present()
            return
        self._apply_language()
        self.controller = self._factory()
        self.controller.signal_listeners.append(self._on_daemon_signal)
        self._build_window()
        self.poll_traffic()
        GLib.timeout_add_seconds(TRAFFIC_POLL_SECONDS, lambda: self.poll_traffic() or True)

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
            self._uninstall_options,
        )
        self.window.present()
        if old is not None:
            old.detach()  # destroying it does not unsubscribe it from the controller
            old.destroy()

    def _notify(self, kind: str, title: str, body: str) -> None:
        """One desktop notification; a newer one of the same kind replaces the older."""
        note = Gio.Notification.new(title)
        note.set_icon(Gio.ThemedIcon.new(APP_ID))
        note.set_body(body)
        self.send_notification(kind, note)
        self.sent.append((kind, body))  # what went out, for the tests

    def _on_daemon_signal(self, name: str, args: tuple) -> None:
        if name == "Reverted":
            self.controller.own_change = ""
            if self.user_settings.wants("reverted"):
                body = _("{profile} was undone because it was not confirmed in time.")
                self._notify("reverted", _("fwmanager"), body.format(
                    profile=state.profile_title(args[0])
                ))  # fmt: skip
        elif name == "Confirmed":
            own = self.controller.own_change == args[0]
            self.controller.own_change = ""
            if not own and self.user_settings.wants("switched"):  # e.g. a network came up
                body = _("Now using the {profile} profile.").format(
                    profile=state.profile_title(args[0])
                )
                self._notify("switched", _("Profile changed"), body)

    # -- traffic ---

    def poll_traffic(self) -> None:
        """Ask the service for new log entries of each kind someone wants to hear about."""
        if self.controller is None:
            return
        for kind, fetch in (
            ("blocked", self.controller.blocked),
            ("allowed", self.controller.allowed),
        ):
            if self.user_settings.wants(kind):
                fetch(
                    lambda result, error, k=kind: error is None and self.traffic.update(k, result)
                )

    def _traffic_notification(self, kind: str, items: list) -> None:
        self._notify(kind, _(traffic.TITLES[kind]), traffic.describe(kind, items))
