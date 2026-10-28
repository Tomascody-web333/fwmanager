"""Settings: language, notifications, blocked-packet logging and router mode."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from fwmanager.i18n import N_, _  # noqa: E402
from fwmanager.ui.common import button, toast, toast_error  # noqa: E402

AUTHOR = "Tomascody-web333"
LANGUAGES = (("pt", "Português"), ("en", "English"))  # always shown in their own language
LOG_LEVELS = (("", N_("Off")), ("low", N_("Low")), ("medium", N_("Medium")), ("high", N_("High")))
NOTIFY_ROWS = (
    ("reverted", N_("A change was undone"), N_("Not confirmed in time, so it was undone.")),
    ("switched", N_("Profile changed by itself"), N_("For example when you join another network.")),
    ("blocked", N_("Blocked connection attempts"), N_("Turns on recording at Low if it is off.")),
    (
        "allowed",
        N_("Connections let in"),
        N_("Turns on recording at Medium if it is lower. Can be frequent."),
    ),
)
# The recording level a kind of traffic notification needs (ufw only logs what it is asked to).
NEEDS_LEVEL = {"blocked": "low", "allowed": "medium"}


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
        general.add(self.language)
        self.add(general)

        # -- notifications: one switch per kind of event ---
        notes = Adw.PreferencesGroup(
            title=_("Notifications"),
            description=_("Choose what fwmanager tells you about while it is open."),
        )
        self.notify_rows: dict[str, Adw.SwitchRow] = {}
        for kind, title, subtitle in NOTIFY_ROWS:
            row = Adw.SwitchRow(
                title=_(title), subtitle=_(subtitle), active=user_settings.wants(kind)
            )
            row.connect("notify::active", lambda r, _p, k=kind: self._notify_changed(k, r))
            self.notify_rows[kind] = row
            notes.add(row)
        self._on_notifications = on_notifications
        self.notifications = self.notify_rows["reverted"]  # the original switch
        self.add(notes)

        firewall = Adw.PreferencesGroup(title=_("Firewall"))
        self.log_level = Adw.ComboRow(
            title=_("Record connections"),
            subtitle=_(
                "Shown in the Log page. Low records blocked attempts; Medium and High also "
                "the connections that were let in."
            ),
            model=Gtk.StringList.new([_(label) for _v, label in LOG_LEVELS]),
        )
        self.router = Adw.SwitchRow(
            title=_("Router mode"),
            subtitle=_(
                "Also filter traffic this computer passes on to other devices: Wi-Fi "
                "hotspot, virtual machines, Docker. Leave it off on an ordinary computer; "
                "turning it on can cut those off."
            ),
            subtitle_lines=0,
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

        credits = Adw.PreferencesGroup()
        self.credits = Gtk.Label(
            label=_("Made by {author} with help of Claude Code").format(author=AUTHOR),
            wrap=True,
            justify=Gtk.Justification.CENTER,
        )
        self.credits.add_css_class("dim-label")
        self.credits.add_css_class("caption")
        credits.add(self.credits)
        self.add(credits)

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

    def _notify_changed(self, kind: str, row: Adw.SwitchRow) -> None:
        self._on_notifications(kind, row.get_active())
        if not row.get_active() or kind not in NEEDS_LEVEL or self._loading:
            return
        levels = [v for v, _l in LOG_LEVELS]
        needed = levels.index(NEEDS_LEVEL[kind])
        if self.log_level.get_selected() < needed:
            self.log_level.set_selected(needed)  # saved to the service like any other change
            toast(self, _("Recording turned on at {level}, so there is something to notify about.")
                  .format(level=_(LOG_LEVELS[needed][1])))  # fmt: skip

    def _save(self) -> None:
        if self._loading:
            return
        level = LOG_LEVELS[self.log_level.get_selected()][0]
        self._c.set_options(self.router.get_active(), level, self._saved)

    def _saved(self, _result, error) -> None:
        if error is not None:
            toast_error(self, error)
            self.load()  # show what is really in effect, not the choice that was refused
