"""Home: state of the firewall, the pending-confirmation card, and quick profile switching."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from fwmanager.i18n import _  # noqa: E402
from fwmanager.ui import state  # noqa: E402
from fwmanager.ui.common import button, clear, label_for_screen_readers, toast_error  # noqa: E402


class HomePage(Adw.PreferencesPage):
    def __init__(self, controller, on_test) -> None:
        super().__init__(title=_("Home"), icon_name="security-high-symbolic")
        self._c = controller
        self._on_test = on_test
        self._profile_rows: list[Adw.ActionRow] = []
        self._profiles: dict[str, dict] = {}
        self._titles: dict[str, str] = {}
        self._pending = ""

        # -- pending confirmation (hidden unless a change is waiting) ---
        self.pending_group = Adw.PreferencesGroup(visible=False)
        self.pending_row = Adw.ActionRow(title=_("Waiting for your confirmation"))
        self.pending_row.add_css_class("warning")
        self.confirm_btn = button(_("Confirm"), suggested=True)
        self.confirm_btn.connect("clicked", lambda *_: self._c.confirm(self._after))
        self.undo_btn = button(_("Undo"))
        self.undo_btn.connect("clicked", lambda *_: self._c.rollback(self._after))
        self.pending_row.add_suffix(self.undo_btn)
        self.pending_row.add_suffix(self.confirm_btn)
        self.pending_group.add(self.pending_row)
        self.add(self.pending_group)

        # -- status ---
        status = Adw.PreferencesGroup(title=_("Status"))
        self.firewall_row = Adw.ActionRow(title=_("Firewall"))
        self.profile_row = Adw.ActionRow(title=_("Active profile"))
        self.network_row = Adw.ActionRow(title=_("Current network"), subtitle=_("Checking…"))
        for row in (self.firewall_row, self.profile_row, self.network_row):
            row.set_subtitle_selectable(False)
            status.add(row)
        self.add(status)

        # -- switch profile ---
        self.switch_group = Adw.PreferencesGroup(
            title=_("Switch profile"),
            description=_("A change is undone after 30 seconds unless you confirm it."),
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

    def _rebuild(self) -> None:
        clear(self.switch_group, self._profile_rows)
        active = (self._c.snapshot.status or {}).get("active_profile", "")
        for name, details in self._profiles.items():
            row = Adw.ActionRow(
                title=state.profile_title(name),
                subtitle=f"{state.profile_hint(name)}\n"
                f"{state.describe_profile(details, self._titles)}".strip(),
                subtitle_lines=3,
            )
            row.name_ = name
            check = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
            label_for_screen_readers(check, _("Active"))
            check.set_opacity(1.0 if name == active else 0.0)  # keeps every row aligned
            row.add_prefix(check)
            test = button(_("Test"), flat=True)
            test.set_tooltip_text(_("Check that this profile will not break your connection"))
            test.connect("clicked", lambda _b, n=name: self._on_test(n))
            apply_btn = button(_("Apply"), suggested=name != active)
            apply_btn.connect("clicked", lambda _b, n=name: self._c.apply(n, self._after))
            row.add_suffix(test)
            row.add_suffix(apply_btn)
            row.apply_btn, row.test_btn, row.check = apply_btn, test, check
            self.switch_group.add(row)
            self._profile_rows.append(row)
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        busy = bool(self._pending)
        active = (self._c.snapshot.status or {}).get("active_profile", "")
        for row in self._profile_rows:
            row.apply_btn.set_sensitive(not busy)
            row.test_btn.set_sensitive(not busy)
            row.check.set_opacity(1.0 if row.name_ == active else 0.0)
            if row.name_ == active:
                row.apply_btn.remove_css_class("suggested-action")
            else:
                row.apply_btn.add_css_class("suggested-action")

    def on_snapshot(self, snap) -> None:
        st = snap.status or {}
        self._pending = st.get("pending_profile", "")
        active = st.get("active_profile", "")
        self.firewall_row.set_subtitle(_("Active") if st.get("firewall_active") else _("Off"))
        self.profile_row.set_subtitle(state.profile_title(active) if active else _("None"))
        self.pending_group.set_visible(bool(self._pending))
        if self._pending:
            self.pending_row.set_subtitle(
                state.countdown_text(self._pending, st.get("seconds_left", 0))
            )
        self._sync_buttons()

    def set_current_network(self, net) -> None:
        if net is None:
            self.network_row.set_subtitle(_("Not connected"))
        elif net.is_wifi:
            self.network_row.set_subtitle(_("Wi-Fi: {name}").format(name=net.ssid or net.name))
        else:
            self.network_row.set_subtitle(_("Wired: {name}").format(name=net.name))
