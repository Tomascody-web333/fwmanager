"""Settings: language, notifications, blocked-packet logging and router mode."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from fwmanager.i18n import N_, _  # noqa: E402
from fwmanager.ui.common import button, toast_error  # noqa: E402

LANGUAGES = (("pt", "Português"), ("en", "English"))  # always shown in their own language
LOG_LEVELS = (("", N_("Off")), ("low", N_("Low")), ("medium", N_("Medium")), ("high", N_("High")))


class SettingsPage(Adw.PreferencesPage):
    def __init__(
        self, controller, user_settings, on_language, on_notifications, on_uninstall=None
    ) -> None:
        super().__init__(title=_("Settings"), icon_name="emblem-system-symbolic")
        self._c = controller
        self._loading = True

        general = Adw.PreferencesGroup(title=_("General"))
        self.language = Adw.ComboRow(
            title=_("Language"),
            subtitle=_("Changes apply right away. No password needed."),
            model=Gtk.StringList.new([name for _code, name in LANGUAGES]),
        )
        codes = [c for c, _n in LANGUAGES]
        from fwmanager.i18n import current_language

        wanted = user_settings.lang or current_language()
        self.language.set_selected(codes.index(wanted) if wanted in codes else codes.index("en"))
        self.language.connect(
            "notify::selected", lambda r, _p: on_language(codes[r.get_selected()])
        )
        self.notifications = Adw.SwitchRow(
            title=_("Notifications"),
            subtitle=_("Tell me when a change was undone automatically."),
            active=user_settings.notifications,
        )
        self.notifications.connect("notify::active", lambda r, _p: on_notifications(r.get_active()))
        general.add(self.language)
        general.add(self.notifications)
        self.add(general)

        firewall = Adw.PreferencesGroup(title=_("Firewall"))
        self.log_level = Adw.ComboRow(
            title=_("Record blocked attempts"),
            subtitle=_("Shown in the Log page. Higher levels record more."),
            model=Gtk.StringList.new([_(label) for _v, label in LOG_LEVELS]),
        )
        self.router = Adw.SwitchRow(
            title=_("Router mode"),
            subtitle=_(
                "Also filter traffic passing through this computer. "
                "May interfere with Docker and virtual machines."
            ),
        )
        self.log_level.connect("notify::selected", lambda *_: self._save())
        self.router.connect("notify::active", lambda *_: self._save())
        firewall.add(self.log_level)
        firewall.add(self.router)
        self.add(firewall)

        remove = Adw.PreferencesGroup(title=_("Uninstall"))
        row = Adw.ActionRow(
            title=_("Uninstall fwmanager"),
            subtitle=_(
                "Removes the app, the command line and the service, and leaves your "
                "firewall as it was before fwmanager."
            ),
            subtitle_lines=0,
        )
        self.uninstall_btn = button(_("Uninstall…"))
        self.uninstall_btn.add_css_class("destructive-action")
        if on_uninstall is not None:
            self.uninstall_btn.connect("clicked", lambda *_: on_uninstall())
        row.add_suffix(self.uninstall_btn)
        remove.add(row)
        self.add(remove)

    def load(self) -> None:
        self._c.options(self._loaded)

    def _loaded(self, result, error) -> None:
        if error is not None:
            toast_error(self, error)
            return
        self._loading = True
        self.router.set_active(result["router_mode"])
        levels = [v for v, _l in LOG_LEVELS]
        self.log_level.set_selected(levels.index(result["log_level"]))
        self._loading = False

    def _save(self) -> None:
        if self._loading:
            return
        level = LOG_LEVELS[self.log_level.get_selected()][0]
        self._c.set_options(self.router.get_active(), level, self._saved)

    def _saved(self, _result, error) -> None:
        if error is not None:
            toast_error(self, error)
            self.load()  # show what is really in effect, not the choice that was refused
