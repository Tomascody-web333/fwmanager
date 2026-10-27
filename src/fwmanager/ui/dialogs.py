"""Dialogs: the profile editor and the "Test policy" dialog."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from fwmanager.errors import FwError  # noqa: E402
from fwmanager.i18n import N_, _  # noqa: E402
from fwmanager.messages import describe_finding, describe_probe  # noqa: E402
from fwmanager.ui import state  # noqa: E402
from fwmanager.ui.common import (  # noqa: E402
    SEVERITY_ICON,
    button,
    clear,
    label_for_screen_readers,
    toast_error,
)

VERDICT_ICON = {
    "safe": "emblem-ok-symbolic",
    "warnings": "dialog-warning-symbolic",
    "unsafe": "dialog-error-symbolic",
}
VERDICT_TEXT = {
    "safe": N_("Safe to use"),
    "warnings": N_("Safe, with warnings"),
    "unsafe": N_("Would break something"),
}
VERDICT_BODY = {
    "safe": N_("Nothing was found that could cut your connection."),
    "warnings": N_("It should work, but read the notes below first."),
    "unsafe": N_("Do not apply this profile as it is."),
}


class ProfileEditor(Adw.Dialog):
    """Services, ping, mDNS and who may connect, for one profile."""

    def __init__(self, controller, details: dict, service_titles: dict[str, str], on_saved) -> None:
        super().__init__(
            title=_("Edit {name}").format(name=state.profile_title(details["name"])),
            content_width=480,
            content_height=640,
        )
        self._c, self._name, self._on_saved = controller, details["name"], on_saved
        self._service_rows: dict[str, Adw.SwitchRow] = {}

        page = Adw.PreferencesPage()
        services = Adw.PreferencesGroup(
            title=_("Services that others may reach"),
            description=_("Everything else coming in from the network stays blocked."),
        )
        for sid, title in sorted(service_titles.items(), key=lambda kv: _(kv[1]).lower()):
            row = Adw.SwitchRow(title=_(title), active=sid in details["services"])
            self._service_rows[sid] = row
            services.add(row)
        page.add(services)

        options = Adw.PreferencesGroup(title=_("Options"))
        self.ping = Adw.SwitchRow(
            title=_("Answer ping"),
            subtitle=_("Lets others check that this computer is on."),
            active=details["ping"],
        )
        self.mdns = Adw.SwitchRow(
            title=_("Be visible on the local network (mDNS)"),
            subtitle=_("Used to find printers and shared devices by name."),
            active=details["mdns"],
        )
        self.source = Adw.ComboRow(
            title=_("Who can connect"),
            model=Gtk.StringList.new([state.source_title(k) for k in state.SOURCE_ORDER]),
            selected=state.SOURCE_ORDER.index(details["source_kind"]),
        )
        self.cidrs = Adw.EntryRow(title=_("Allowed networks (e.g. 192.168.1.0/24)"))
        self.cidrs.set_text(", ".join(details["cidrs"]))
        self.source.connect("notify::selected", lambda *_: self._sync())
        for w in (self.ping, self.mdns, self.source, self.cidrs):
            options.add(w)
        page.add(options)

        reset = Adw.PreferencesGroup()
        self.reset_btn = button(_("Restore the original settings"), flat=True)
        self.reset_btn.connect("clicked", self._reset)
        reset.add(self.reset_btn)
        page.add(reset)

        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = button(_("Cancel"))
        cancel.connect("clicked", lambda *_: self.close())
        self.save_btn = button(_("Save"), suggested=True)
        self.save_btn.connect("clicked", self._save)
        header.pack_start(cancel)
        header.pack_end(self.save_btn)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(page)
        self.set_child(view)
        self._sync()

    def _kind(self) -> str:
        return state.SOURCE_ORDER[self.source.get_selected()]

    def _sync(self) -> None:
        self.cidrs.set_visible(self._kind() == "cidrs")

    def _save(self, *_args) -> None:
        chosen = [sid for sid, row in self._service_rows.items() if row.get_active()]
        cidrs = state.parse_cidrs(self.cidrs.get_text()) if self._kind() == "cidrs" else []
        self.save_btn.set_sensitive(False)
        self._c.save_profile(
            self._name, chosen, self.ping.get_active(), self.mdns.get_active(),
            self._kind(), cidrs, self._saved,
        )  # fmt: skip

    def _saved(self, _result, error) -> None:
        self.save_btn.set_sensitive(True)
        if error is not None:
            toast_error(self, error)
            return
        self.close()
        self._on_saved()

    def _reset(self, *_args) -> None:
        self._c.reset_profile(self._name, self._saved)


class PolicyTestDialog(Adw.Dialog):
    """Runs the static and live checks for a profile and explains the verdict."""

    def __init__(self, controller, profile: str, on_apply) -> None:
        super().__init__(
            title=_("Test {name}").format(name=state.profile_title(profile)),
            content_width=520,
            content_height=620,
        )
        self._c, self._profile, self._on_apply = controller, profile, on_apply
        self.outcome = None

        self.stack = Gtk.Stack()
        busy = Adw.StatusPage(
            title=_("Testing…"),
            description=_(
                "The profile is applied for a few seconds while your connection is "
                "measured, then everything goes back to how it was."
            ),
        )
        spinner = Gtk.Spinner(spinning=True, width_request=48, height_request=48)
        busy.set_child(spinner)
        self.stack.add_named(busy, "busy")
        self.result = Adw.PreferencesPage()
        self.stack.add_named(self.result, "result")

        self.verdict_group = Adw.PreferencesGroup()
        self.verdict_row = Adw.ActionRow()
        self.verdict_icon = Gtk.Image(pixel_size=32)
        self.verdict_row.add_prefix(self.verdict_icon)
        self.verdict_group.add(self.verdict_row)
        self.result.add(self.verdict_group)
        self.checks = Adw.PreferencesGroup(title=_("What was checked"))
        self.live = Adw.PreferencesGroup(title=_("Your connection, before and after"))
        self.result.add(self.checks)
        self.result.add(self.live)
        self._rows: list = []
        self._live_rows: list = []

        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        close = button(_("Close"))
        close.connect("clicked", lambda *_: self.close())
        self.apply_btn = button(_("Apply anyway"), suggested=True)
        self.apply_btn.set_sensitive(False)
        self.apply_btn.connect("clicked", self._apply)
        header.pack_start(close)
        header.pack_end(self.apply_btn)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(self.stack)
        self.set_child(view)
        self.stack.set_visible_child_name("busy")

    def start(self, static_only: bool = False) -> None:
        self._c.test_policy(self._profile, static_only, self._done)

    def _done(self, outcome, error: FwError | None) -> None:
        if error is not None:
            self.close()
            toast_error(self._parent_widget(), error)
            return
        self.show_outcome(outcome)

    def _parent_widget(self):
        return self.get_parent() or self

    def show_outcome(self, outcome) -> None:
        self.outcome = outcome
        v = outcome.verdict
        self.verdict_icon.set_from_icon_name(VERDICT_ICON[v])
        self.verdict_row.set_title(_(VERDICT_TEXT[v]))
        self.verdict_row.set_subtitle(_(VERDICT_BODY[v]))
        label_for_screen_readers(self.verdict_row, _(VERDICT_TEXT[v]))
        for cls in ("success", "warning", "error"):
            self.verdict_row.remove_css_class(cls)
        self.verdict_row.add_css_class(
            {"safe": "success", "warnings": "warning", "unsafe": "error"}[v]
        )

        clear(self.checks, self._rows)
        for f in outcome.findings:
            row = Adw.ActionRow(
                title=describe_finding(f.code, f.detail), subtitle_lines=3, title_lines=3
            )
            if f.code in ("will_add", "will_remove"):
                row.set_subtitle(f.detail)
            icon = Gtk.Image.new_from_icon_name(SEVERITY_ICON[f.severity])
            icon.add_css_class(
                {"error": "error", "warning": "warning", "info": "success"}[f.severity]
            )
            label_for_screen_readers(icon, {"error": _("Error"), "warning": _("Warning"),
                                            "info": _("Note")}[f.severity])  # fmt: skip
            row.add_prefix(icon)
            self.checks.add(row)
            self._rows.append(row)
        if not outcome.findings:
            row = Adw.ActionRow(title=_("No problems found in the rules."))
            self.checks.add(row)
            self._rows.append(row)

        clear(self.live, self._live_rows)
        self.live.set_visible(outcome.live is not None)
        if outcome.live is not None:
            marks = {True: _("works"), False: _("fails"), None: _("not applicable")}
            for before, after in zip(outcome.live.before, outcome.live.after, strict=False):
                row = Adw.ActionRow(title=describe_probe(before.name))
                broken = before.ok is True and after.ok is False
                row.set_subtitle(
                    _("{before} → {after}").format(before=marks[before.ok], after=marks[after.ok])
                )
                icon = Gtk.Image.new_from_icon_name(
                    "dialog-error-symbolic" if broken else "emblem-ok-symbolic"
                )
                icon.set_visible(before.ok is not None)
                icon.add_css_class("error" if broken else "success")
                label_for_screen_readers(icon, _("Broken") if broken else _("Fine"))
                row.add_prefix(icon)
                self.live.add(row)
                self._live_rows.append(row)

        self.apply_btn.set_sensitive(outcome.can_apply)
        self.apply_btn.set_label(_("Apply") if v == "safe" else _("Apply anyway"))
        self.stack.set_visible_child_name("result")

    def _apply(self, *_args) -> None:
        if self.outcome is None or not self.outcome.can_apply:
            return  # an unsafe profile is never applied from here, whatever was clicked
        self.close()
        self._on_apply(self._profile)
