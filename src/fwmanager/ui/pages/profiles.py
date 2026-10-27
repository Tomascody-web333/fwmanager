"""Profiles: look at and edit what each profile opens."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from fwmanager.i18n import _  # noqa: E402
from fwmanager.ui import state  # noqa: E402
from fwmanager.ui.common import clear, toast, toast_error  # noqa: E402
from fwmanager.ui.dialogs import ProfileEditor  # noqa: E402


class ProfilesPage(Adw.PreferencesPage):
    def __init__(self, controller, on_changed) -> None:
        super().__init__(title=_("Profiles"), icon_name="document-edit-symbolic")
        self._c, self._on_changed = controller, on_changed
        self._rows: list[Adw.ActionRow] = []
        self._profiles: dict[str, dict] = {}
        self._titles: dict[str, str] = {}
        self.group = Adw.PreferencesGroup(
            title=_("Profiles"),
            description=_("Choose what each profile lets in. Select one to edit it."),
        )
        self.add(self.group)

    def load(self) -> None:
        self._c.load_profiles(self._loaded)

    def _loaded(self, result, error) -> None:
        if error is not None:
            toast_error(self, error)
            return
        self._profiles, self._titles = result
        clear(self.group, self._rows)
        for name, details in self._profiles.items():
            row = Adw.ActionRow(
                title=state.profile_title(name),
                subtitle=state.describe_profile(details, self._titles),
                subtitle_lines=3,
                activatable=True,
            )
            for flag, text in (
                (details["customized"], _("edited")),
                (details.get("affected_by_rules"), _("rules apply")),
            ):
                if flag:
                    tag = Gtk.Label(label=text, valign=Gtk.Align.CENTER)
                    tag.add_css_class("dim-label")
                    row.add_suffix(tag)
            row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            row.connect("activated", lambda _r, n=name: self._edit(n))
            self.group.add(row)
            self._rows.append(row)

    def _edit(self, name: str) -> None:
        dialog = ProfileEditor(self._c, self._profiles[name], self._titles, self._saved)
        dialog.present(self.get_root())

    def _saved(self) -> None:
        toast(self, _("Profile saved. Apply it again to use the changes."))
        self.load()
        self._on_changed()
