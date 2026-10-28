"""Dialogs: the profile editor and the "Test policy" dialog."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from fwmanager.errors import FwError  # noqa: E402
from fwmanager.i18n import N_, _  # noqa: E402
from fwmanager.messages import (  # noqa: E402
    describe_error,
    describe_finding,
    describe_probe,
    finding_detail,
)
from fwmanager.ui import rule_text, state  # noqa: E402
from fwmanager.ui.common import (  # noqa: E402
    SEVERITY_ICON,
    button,
    clear,
    label_for_screen_readers,
    show_warning,
    toast_error,
    warning_row,
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
        errors = Adw.PreferencesGroup()
        self.error_row = warning_row()  # what the service refused, inside the dialog
        errors.add(self.error_row)
        # Changed from the defaults: say so up front, with the way back.
        self.customized_row = Adw.ActionRow(
            title=_("You changed this profile."), visible=bool(details.get("customized"))
        )
        restore = button(_("Restore defaults"))
        restore.connect("clicked", self._reset)
        self.customized_row.add_suffix(restore)
        errors.add(self.customized_row)
        page.add(errors)
        services = Adw.PreferencesGroup(
            title=_("What this profile lets in"),
            description=_(
                "Switch a service off to block it. Anything not on this list is always blocked."
            ),
        )
        for sid, title in sorted(service_titles.items(), key=lambda kv: _(kv[1]).lower()):
            row = Adw.SwitchRow(title=_(title), active=sid in details["services"])
            row.connect("notify::active", lambda r, _p: self._show_allowed(r))
            self._show_allowed(row)
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
        self.cidr_problem = warning_row()
        self.source.connect("notify::selected", lambda *_: self._sync())
        self.cidrs.connect("changed", lambda *_: self._sync())
        for w in (self.ping, self.mdns, self.source, self.cidrs, self.cidr_problem):
            options.add(w)
        page.add(options)

        reset = Adw.PreferencesGroup()
        self.reset_btn = button(_("Restore the original settings"), flat=True)
        self.reset_btn.set_sensitive(bool(details.get("customized")))  # nothing to restore yet
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

    @staticmethod
    def _show_allowed(row: Adw.SwitchRow) -> None:
        row.set_subtitle(_("Allowed") if row.get_active() else _("Blocked"))

    def _kind(self) -> str:
        return state.SOURCE_ORDER[self.source.get_selected()]

    def _sync(self) -> None:
        wanted = self._kind() == "cidrs"
        self.cidrs.set_visible(wanted)
        problem = state.cidr_problem(self.cidrs.get_text()) if wanted else ""
        show_warning(self.cidr_problem, problem)
        if problem:
            self.cidrs.add_css_class("error")
        else:
            self.cidrs.remove_css_class("error")
        if hasattr(self, "save_btn"):  # built after the options
            self.save_btn.set_sensitive(not problem)

    def _save(self, *_args) -> None:
        chosen = [sid for sid, row in self._service_rows.items() if row.get_active()]
        cidrs = state.parse_cidrs(self.cidrs.get_text()) if self._kind() == "cidrs" else []
        self.save_btn.set_sensitive(False)
        self._c.save_profile(
            self._name, chosen, self.ping.get_active(), self.mdns.get_active(),
            self._kind(), cidrs, self._saved,
        )  # fmt: skip

    def _saved(self, _result, error) -> None:
        self._sync()
        if error is not None:
            show_warning(self.error_row, describe_error(error))
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
            row.set_subtitle(finding_detail(f.code, f.detail))
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


class RuleEditor(Adw.Dialog):
    """Build a rule from a template: pick it, fill the blanks, read the sentence and the preview."""

    def __init__(self, controller, profile_names: list[str], titles: dict[str, str], on_saved):
        super().__init__(title=_("Add a rule"), content_width=520, content_height=820)
        self._c, self._names, self._titles = controller, profile_names, titles
        self._on_saved = on_saved
        self._templates = list(rule_text.rb.TEMPLATE_ORDER)
        self._service_ids = [
            sid for sid, _t in sorted(titles.items(), key=lambda kv: _(kv[1]).lower())
        ]
        self._serial = 0  # answers to older previews are ignored
        self._detail_rows: list = []
        self._preview_rows: list = []
        self.params: dict | None = None

        page = Adw.PreferencesPage()
        pick = Adw.PreferencesGroup(title=_("What do you want to do?"))
        self.template = Adw.ComboRow(
            title=_("Rule type"),
            model=Gtk.StringList.new([rule_text.template_title(t) for t in self._templates]),
        )
        pick.add(self.template)
        page.add(pick)

        # The finished sentence and its preview come first, so they are on screen while the
        # blanks below are filled in (they update live).
        self.sentence_group = Adw.PreferencesGroup(title=_("The rule"))
        self.sentence_row = Adw.ActionRow(title=_("Choose the details below."), title_lines=4)
        self.sentence_group.add(self.sentence_row)
        self.error_row = warning_row()  # what the service refused when saving
        self.sentence_group.add(self.error_row)
        page.add(self.sentence_group)

        self.preview = Adw.PreferencesGroup(
            title=_("What will happen"),
            description=_("Nothing is changed on your computer until you apply a profile."),
        )
        page.add(self.preview)

        self.details = Adw.PreferencesGroup(title=_("Details"))
        page.add(self.details)

        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = button(_("Cancel"))
        cancel.connect("clicked", lambda *_: self.close())
        self.save_btn = button(_("Save rule"), suggested=True)
        self.save_btn.set_sensitive(False)
        self.save_btn.connect("clicked", self._save)
        header.pack_start(cancel)
        header.pack_end(self.save_btn)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(page)
        self.set_child(view)

        self.template.connect("notify::selected", lambda *_: self._build_details())
        self._build_details()

    # -- the blanks, per template ---

    @property
    def current(self) -> str:
        return self._templates[self.template.get_selected()]

    def _build_details(self) -> None:
        clear(self.details, self._detail_rows)
        self.service_combo = self.service_switches = self.profile_switches = None
        self.scope = self.cidrs = None
        kind = self.current
        rb = rule_text.rb
        if kind in (rb.ALLOW_ONLY_IN, rb.CLOSE_SERVICE):
            self.service_combo = Adw.ComboRow(
                title=_("Service"),
                model=Gtk.StringList.new([_(self._titles[s]) for s in self._service_ids]),
            )
            self._add(self.service_combo)
        if kind == rb.BLOCK_ALL_EXCEPT:
            self.service_switches = {}
            for sid in self._service_ids:
                row = Adw.SwitchRow(title=_(self._titles[sid]))
                row.connect("notify::active", lambda *_: self._changed())
                self.service_switches[sid] = row
                self._add(row)
        if kind == rb.RESTRICT_SOURCE:
            self.scope = Adw.ComboRow(
                title=_("Accept connections from"),
                model=Gtk.StringList.new([_("The local network"), _("Specific networks")]),
            )
            self.cidrs = Adw.EntryRow(title=_("Networks (e.g. 192.168.1.0/24)"), visible=False)
            self.cidrs.connect("changed", lambda *_: self._changed())
            self.scope.connect("notify::selected", lambda *_: self._scope_changed())
            self._add(self.scope)
            self._add(self.cidrs)
        if kind != rb.CLOSE_SERVICE:
            self.profile_switches = {}
            for name in self._names:
                row = Adw.SwitchRow(title=state.profile_title(name), active=name == "casa")
                row.connect("notify::active", lambda *_: self._changed())
                self.profile_switches[name] = row
                self._add(row)
        if self.service_combo is not None:
            self.service_combo.connect("notify::selected", lambda *_: self._changed())
        self._changed()

    def _add(self, row) -> None:
        self.details.add(row)
        self._detail_rows.append(row)

    def _scope_changed(self) -> None:
        self.cidrs.set_visible(self.scope.get_selected() == 1)
        self._changed()

    # -- reading the blanks, then the sentence and the preview ---

    def _collect(self) -> dict | None:
        """The parameters as the daemon wants them, or ``None`` while something is missing."""
        rb = rule_text.rb
        params: dict = {}
        if self.service_combo is not None:
            params["service"] = self._service_ids[self.service_combo.get_selected()]
        if self.service_switches is not None:
            params["services"] = [s for s, r in self.service_switches.items() if r.get_active()]
        if self.profile_switches is not None:
            chosen = [n for n, r in self.profile_switches.items() if r.get_active()]
            if not chosen:
                return None
            params["profiles"] = chosen
        if self.scope is not None:
            if self.scope.get_selected() == 0:
                params["scope"] = "lan"
            else:
                cidrs = state.parse_cidrs(self.cidrs.get_text())
                if not cidrs:
                    return None
                params["scope"], params["cidrs"] = "cidrs", cidrs
        return params if self.current in rb.SCHEMAS else None

    def _changed(self) -> None:
        self.params = self._collect()
        self.save_btn.set_sensitive(False)
        if self.params is None:
            self.sentence_row.set_title(_("Choose the details below."))
            clear(self.preview, self._preview_rows)
            return
        self.sentence_row.set_title(rule_text.sentence(self.current, self.params, self._titles))
        self._serial += 1
        serial = self._serial
        self._c.preview_rule(
            self.current, self.params, lambda r, e, s=serial: self._previewed(s, r, e)
        )

    def _previewed(self, serial: int, changes, error: FwError | None) -> None:
        if serial != self._serial:
            return  # the blanks changed again meanwhile
        clear(self.preview, self._preview_rows)
        if error is not None:
            row = Adw.ActionRow(title=_("This rule cannot be saved: check the details."))
            row.add_prefix(Gtk.Image.new_from_icon_name("dialog-warning-symbolic"))
            self.preview.add(row)
            self._preview_rows.append(row)
            return
        for line in rule_text.preview_lines(changes, self._titles):
            row = Adw.ActionRow(title=line, title_lines=4)
            self.preview.add(row)
            self._preview_rows.append(row)
        self.save_btn.set_sensitive(True)

    def _save(self, *_args) -> None:
        if self.params is None:
            return
        self.save_btn.set_sensitive(False)
        self._c.add_rule(self.current, self.params, self._saved)

    def _saved(self, _result, error: FwError | None) -> None:
        if error is not None:
            self.save_btn.set_sensitive(True)
            show_warning(self.error_row, describe_error(error))
            return
        self.close()
        self._on_saved()
