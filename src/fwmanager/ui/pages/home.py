"""Overview: how protected the computer is, the pending-confirmation card, and the profiles.

Everything about profiles lives here: switching, testing and editing, so a beginner has one
place to look.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from fwmanager.i18n import _  # noqa: E402
from fwmanager.ui import state  # noqa: E402
from fwmanager.ui.common import (  # noqa: E402
    button,
    clear,
    label_for_screen_readers,
    toast,
    toast_error,
)
from fwmanager.ui.dialogs import ProfileEditor  # noqa: E402


class HomePage(Adw.PreferencesPage):
    def __init__(self, controller, on_test, on_profile_saved=lambda: None) -> None:
        super().__init__(title=_("Overview"), icon_name="security-high-symbolic")
        self._c = controller
        self._on_test = on_test
        self._on_profile_saved = on_profile_saved
        self._profile_rows: list[Adw.ActionRow] = []
        self._profiles: dict[str, dict] = {}
        self._titles: dict[str, str] = {}
        self._pending = ""

        # -- pending confirmation (hidden unless a change is waiting) ---
        self.pending_group = Adw.PreferencesGroup(visible=False)
        self.pending_row = Adw.ActionRow(title=_("Is your connection still working?"))
        self.pending_row.add_css_class("warning")
        self.confirm_btn = button(_("Yes, keep it"), suggested=True)
        self.confirm_btn.connect("clicked", lambda *_: self._c.confirm(self._after))
        self.undo_btn = button(_("No, undo it"))
        self.undo_btn.connect("clicked", lambda *_: self._c.rollback(self._after))
        self.pending_row.add_suffix(self.undo_btn)
        self.pending_row.add_suffix(self.confirm_btn)
        self.pending_group.add(self.pending_row)
        self.add(self.pending_group)

        # -- status: one sentence, what it means, and where you are ---
        status = Adw.PreferencesGroup(title=_("Status"))
        self.status_icon = Gtk.Image.new_from_icon_name("security-high-symbolic")
        self.status_icon.set_pixel_size(32)
        self.status_row = Adw.ActionRow(title=_("Checking…"), subtitle_lines=3)
        self.status_row.add_prefix(self.status_icon)
        self.network_row = Adw.ActionRow(title=_("Current network"), subtitle=_("Checking…"))
        for row in (self.status_row, self.network_row):
            row.set_subtitle_selectable(False)
            status.add(row)
        self.add(status)

        # -- profiles ---
        self.switch_group = Adw.PreferencesGroup(
            title=_("Profiles"),
            description=_(
                "Pick how open this computer should be. After a switch you have 30 seconds "
                "to confirm that everything still works, otherwise it is undone."
            ),
        )
        self.add(self.switch_group)

    # -- updates ---

    def _after(self, _result, error) -> None:
        if error is not None:
            toast_error(self, error)

    def load(self) -> None:
        self._c.load_profiles(self._on_profiles)

    def _on_profiles(self, result, error) -> None:
        if error is not None:
            toast_error(self, error)
            return
        self._profiles, self._titles = result
        self._rebuild()
        self._update_status()

    def _rebuild(self) -> None:
        clear(self.switch_group, self._profile_rows)
        for name, details in self._profiles.items():
            notes = [state.profile_hint(name), state.describe_profile(details, self._titles)]
            if details.get("customized"):
                notes.append(_("You edited this profile."))
            if details.get("affected_by_rules"):
                notes.append(_("Your rules change this profile."))
            row = Adw.ActionRow(
                title=state.profile_title(name),
                subtitle="\n".join(n for n in notes if n),
                subtitle_lines=0,  # never cut what a profile lets in
            )
            row.name_ = name
            check = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
            label_for_screen_readers(check, _("In use"))
            row.add_prefix(check)
            edit = button("", flat=True, icon="document-edit-symbolic")
            edit.set_tooltip_text(_("Change what this profile allows and blocks"))
            label_for_screen_readers(edit, _("Edit {profile}").format(profile=row.get_title()))
            edit.connect("clicked", lambda _b, n=name: self.edit_profile(n))
            test = button(_("Test"), flat=True)
            test.set_tooltip_text(_("Check that this profile will not break your connection"))
            test.connect("clicked", lambda _b, n=name: self._on_test(n))
            in_use = Gtk.Label(label=_("In use"), valign=Gtk.Align.CENTER)
            in_use.add_css_class("dim-label")
            applying = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER, visible=False)
            applying.append(Gtk.Spinner(spinning=True))
            applying.append(Gtk.Label(label=_("Applying…")))
            apply_btn = button(_("Use"), suggested=True)
            apply_btn.set_tooltip_text(_("Switch to this profile now"))
            apply_btn.connect("clicked", lambda _b, n=name: self._c.apply(n, self._after))
            for widget in (edit, test, in_use, applying, apply_btn):
                row.add_suffix(widget)
            row.apply_btn, row.test_btn, row.edit_btn = apply_btn, test, edit
            row.check, row.in_use, row.applying = check, in_use, applying
            self.switch_group.add(row)
            self._profile_rows.append(row)
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        applying = self._c.applying
        busy = bool(self._pending or applying)
        active = (self._c.snapshot.status or {}).get("active_profile", "")
        for row in self._profile_rows:
            current = row.name_ == active and not applying
            row.applying.set_visible(row.name_ == applying)
            # nothing to switch to while it is already in use, or while it is being applied
            row.apply_btn.set_visible(not current and row.name_ != applying)
            row.in_use.set_visible(current)
            row.apply_btn.set_sensitive(not busy)
            row.test_btn.set_sensitive(not busy)
            row.check.set_opacity(1.0 if current else 0.0)  # keeps every row aligned

    def _update_status(self) -> None:
        st = self._c.snapshot.status or {}
        active = st.get("active_profile", "")
        on = bool(st.get("firewall_active"))
        self.status_row.set_title(state.protection_title(st))
        self.status_icon.set_from_icon_name(
            "security-high-symbolic" if on else "security-low-symbolic"
        )
        if not on:
            self.status_row.set_subtitle(_("Choose a profile below to turn it on."))
        elif active in self._profiles:
            self.status_row.set_subtitle(
                state.describe_profile(self._profiles[active], self._titles)
            )
        else:
            self.status_row.set_subtitle("")

    def on_snapshot(self, snap) -> None:
        st = snap.status or {}
        self._pending = st.get("pending_profile", "")
        self.pending_group.set_visible(bool(self._pending))
        if self._pending:
            self.pending_row.set_subtitle(
                state.countdown_text(self._pending, st.get("seconds_left", 0))
            )
        self._update_status()
        self._sync_buttons()

    def set_current_network(self, net) -> None:
        self.network_row.set_subtitle(state.network_text(net))

    # -- editing ---

    def edit_profile(self, name: str) -> ProfileEditor:
        dialog = ProfileEditor(self._c, self._profiles[name], self._titles, self._saved)
        dialog.present(self.get_root())
        return dialog

    def _saved(self) -> None:
        toast(self, _("Profile saved. Use it again to apply the changes."))
        self.load()
        self._on_profile_saved()
