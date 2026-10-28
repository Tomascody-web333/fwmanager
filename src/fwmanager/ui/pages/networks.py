"""Networks: which profile to use on which Wi-Fi (or wired) connection."""

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
    show_warning,
    toast,
    toast_error,
    warning_row,
)


class NetworksPage(Adw.PreferencesPage):
    def __init__(self, controller) -> None:
        super().__init__(title=_("Networks"), icon_name="network-wireless-symbolic")
        self._c = controller
        self._names = [n for n in state.PROFILE_ORDER]
        self._current = None
        self._saved: list[tuple[str, str, str]] = []
        self._rows: list[Adw.ActionRow] = []

        self.current_group = Adw.PreferencesGroup(
            title=_("This network"),
            description=_("Use the same profile every time you connect here."),
        )
        self.current_row = Adw.ComboRow(
            title=_("Not connected"),
            model=Gtk.StringList.new([state.profile_title(n) for n in self._names]),
            selected=self._names.index("public"),
        )
        self.remember_btn = button(_("Remember"), suggested=True)
        self.remember_btn.connect("clicked", self._remember_current)
        self.current_row.add_suffix(self.remember_btn)
        self.current_row.connect("notify::selected", lambda *_: self._update_open_warning())
        self.current_group.add(self.current_row)
        self.open_warning = warning_row()
        self.current_group.add(self.open_warning)
        self.add(self.current_group)

        self.saved_group = Adw.PreferencesGroup(
            title=_("Remembered networks"),
            description=_(
                "Wi-Fi networks you have not chosen a profile for use the Public profile. "
                "A cable keeps the current profile."
            ),
        )
        self.add(self.saved_group)

        self.add_group = Adw.PreferencesGroup(title=_("Add a network by name"))
        self.ssid_entry = Adw.EntryRow(title=_("Wi-Fi name"))
        self.add_profile = Adw.ComboRow(
            title=_("Profile"),
            model=Gtk.StringList.new([state.profile_title(n) for n in self._names]),
            selected=self._names.index("home"),
        )
        self.add_btn = button(_("Add"), suggested=True)
        self.add_btn.connect("clicked", self._add_manual)
        self.add_profile.add_suffix(self.add_btn)
        self.add_group.add(self.ssid_entry)
        self.add_group.add(self.add_profile)
        self.add(self.add_group)

    def load(self) -> None:
        self._c.networks(self._loaded)
        self._c.current_network(self._current_loaded)

    def _loaded(self, result, error) -> None:
        if error is not None:
            toast_error(self, error)
            return
        clear(self.saved_group, self._rows)
        self._saved = list(result)
        self._preselect()
        for ssid, uuid, profile in result:
            row = Adw.ActionRow(
                title=ssid or _("Connection {id}").format(id=uuid[:8]),
            )
            combo = Gtk.DropDown.new_from_strings([state.profile_title(n) for n in self._names])
            combo.set_valign(Gtk.Align.CENTER)
            if profile in self._names:
                combo.set_selected(self._names.index(profile))
            combo.connect(
                "notify::selected",
                lambda c, _p, s=ssid, u=uuid: self._c.set_network(
                    s, u, self._names[c.get_selected()], self._after
                ),
            )
            label_for_screen_readers(combo, _("Profile for {name}").format(name=ssid or uuid))
            remove = button("", flat=True, icon="user-trash-symbolic")
            remove.set_tooltip_text(_("Forget this network"))
            label_for_screen_readers(remove, _("Forget this network"))
            remove.connect("clicked", lambda _b, n=(ssid, uuid, profile): self.forget(*n))
            row.add_suffix(combo)
            row.add_suffix(remove)
            self.saved_group.add(row)
            self._rows.append(row)
        self.saved_group.set_visible(bool(self._rows))

    def _current_loaded(self, net, _error) -> None:
        self._current = net
        self.current_group.set_visible(net is not None)
        if net is not None:
            self.current_row.set_title(net.ssid or net.name)
            self.current_row.set_subtitle(
                _("No password: anyone nearby can join") if net.open else ""
            )
            self._preselect()
        self._update_open_warning()

    def _update_open_warning(self) -> None:
        profile = self._names[self.current_row.get_selected()]
        show_warning(self.open_warning, state.open_wifi_warning(self._current, profile))

    def _preselect(self) -> None:
        """Show the profile already remembered for the current network, if any."""
        net = self._current
        if net is None:
            return
        by_uuid = [p for s, u, p in self._saved if u and u == net.uuid]
        by_ssid = [p for s, u, p in self._saved if net.ssid and s == net.ssid and not u]
        for profile in by_uuid + by_ssid:
            if profile in self._names:
                self.current_row.set_selected(self._names.index(profile))
                return

    def _after(self, _result, error) -> None:
        if error is not None:
            toast_error(self, error)

    def forget(self, ssid: str, uuid: str, profile: str) -> None:
        """Forget at once, with a way back: one wrong click should not cost a setting."""

        def removed(_result, error) -> None:
            self._after(_result, error)
            self.load()
            if error is None:
                self._offer_undo(ssid, uuid, profile)

        self._c.remove_network(ssid, uuid, removed)

    def _offer_undo(self, ssid: str, uuid: str, profile: str) -> None:
        overlay = getattr(self.get_root(), "toasts", None)
        if overlay is None:
            return
        name = ssid or _("Connection {id}").format(id=uuid[:8])
        self.undo_toast = Adw.Toast(
            title=_("Forgot {name}.").format(name=name), button_label=_("Undo"), timeout=8
        )
        self.undo_toast.connect(
            "button-clicked", lambda *_: self._c.set_network(ssid, uuid, profile, self._removed)
        )
        overlay.add_toast(self.undo_toast)

    def _removed(self, _result, error) -> None:
        self._after(_result, error)
        self.load()

    def _remember_current(self, *_args) -> None:
        net = self._current
        if net is None:
            return
        profile = self._names[self.current_row.get_selected()]
        self._c.set_network(net.ssid, net.uuid, profile, self._remembered)

    def _remembered(self, _result, error) -> None:
        self._after(_result, error)
        if error is None:
            toast(self, _("Remembered."))
        self.load()

    def _add_manual(self, *_args) -> None:
        ssid = self.ssid_entry.get_text().strip()
        if not ssid:
            return
        profile = self._names[self.add_profile.get_selected()]
        self._c.set_network(ssid, "", profile, self._added)

    def _added(self, _result, error) -> None:
        self._after(_result, error)
        if error is None:
            self.ssid_entry.set_text("")
        self.load()
