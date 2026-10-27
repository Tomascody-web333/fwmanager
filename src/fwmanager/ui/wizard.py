"""First-run setup, inside the window: language, what is missing, a profile for this network."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from fwmanager.i18n import _  # noqa: E402
from fwmanager.ui import privileged, relaunch, setup_state, state  # noqa: E402
from fwmanager.ui.common import (  # noqa: E402
    button,
    clear,
    label_for_screen_readers,
    show_warning,
    toast,
    toast_error,
    warning_row,
)
from fwmanager.ui.dialogs import PolicyTestDialog  # noqa: E402
from fwmanager.ui.pages.settings import LANGUAGES  # noqa: E402
from fwmanager.ui.worker import run_async  # noqa: E402

STAGES = ("welcome", "requirements", "profile", "done")
CHOICES = ("casa", "trabalho", "publico")  # the three everyday profiles
STATE_ICON = {
    setup_state.OK: ("emblem-ok-symbolic", "success"),
    setup_state.MISSING: ("dialog-warning-symbolic", "warning"),
    setup_state.RESTART: ("view-refresh-symbolic", "warning"),
    setup_state.UNKNOWN: ("content-loading-symbolic", "dim-label"),
}


def _state_text(req: setup_state.Requirement) -> str:
    if req.state == setup_state.OK:
        return _("Ready")
    if req.state == setup_state.RESTART:
        return _("Allowed. fwmanager has to be restarted to notice it.")
    if req.state == setup_state.UNKNOWN:
        return _("Waiting for the service")
    return _("Needs attention")


class SetupWizard(Adw.Bin):
    def __init__(
        self,
        controller,
        user_settings,
        user: str,
        on_language,
        on_stage,
        on_finished,
        run_helper=privileged.run_setup_helper,
        do_relaunch=relaunch.relaunch,
        group_info=setup_state.group_info,
        ufw_present=setup_state.ufw_on_path,
        run=run_async,
    ) -> None:
        super().__init__()
        self._c, self._user = controller, user
        self._on_language, self._on_stage, self._on_finished = on_language, on_stage, on_finished
        self._run_helper, self._relaunch = run_helper, do_relaunch
        self._group_info, self._ufw_present, self._run = group_info, ufw_present, run
        self._rows: list[Adw.ActionRow] = []
        self._choice_rows: dict[str, Gtk.CheckButton] = {}
        self._network = None
        self._applied: str | None = None
        self.helper_running = False

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT)
        self.stack.add_named(self._welcome_page(user_settings), "welcome")
        self.stack.add_named(self._requirements_page(), "requirements")
        self.stack.add_named(self._profile_page(), "profile")
        self.stack.add_named(self._done_page(), "done")
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.skip_btn = button(_("Skip setup"), flat=True)
        self.skip_btn.connect("clicked", lambda *_: self.finish())
        header.pack_end(self.skip_btn)
        view.add_top_bar(header)
        view.set_content(self.stack)
        self.set_child(view)
        self.go(user_settings.setup_stage if user_settings.setup_stage != "done" else "welcome")

    # -- navigation ---

    @property
    def stage(self) -> str:
        return self.stack.get_visible_child_name()

    def go(self, stage: str) -> None:
        self.stack.set_visible_child_name(stage)
        self._on_stage(stage)
        if stage == "requirements":
            self.refresh_requirements()
        elif stage == "profile":
            self._c.current_network(self._network_loaded)

    def finish(self) -> None:
        self._on_stage("done")
        self._on_finished()

    # -- 1. welcome ---

    def _welcome_page(self, user_settings) -> Adw.StatusPage:
        page = Adw.StatusPage(
            icon_name="security-high-symbolic",
            title=_("Welcome to fwmanager"),
            description=_(
                "A friendly face for the ufw firewall. Setup takes about a minute and "
                "needs no terminal."
            ),
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, halign=Gtk.Align.CENTER)
        group = Adw.PreferencesGroup()
        codes = [c for c, _n in LANGUAGES]
        from fwmanager.i18n import current_language

        wanted = user_settings.lang or current_language()
        self.language = Adw.ComboRow(
            title=_("Language"),
            model=Gtk.StringList.new([name for _c, name in LANGUAGES]),
            selected=codes.index(wanted) if wanted in codes else codes.index("en"),
        )
        self.language.connect(
            "notify::selected", lambda r, _p: self._on_language(codes[r.get_selected()])
        )
        group.add(self.language)
        group.set_size_request(380, -1)
        self.start_btn = button(_("Get started"), suggested=True)
        self.start_btn.add_css_class("pill")
        self.start_btn.set_halign(Gtk.Align.CENTER)
        self.start_btn.connect("clicked", lambda *_: self.go("requirements"))
        box.append(group)
        box.append(self.start_btn)
        page.set_child(box)
        return page

    # -- 2. requirements ---

    def _requirements_page(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()
        self.req_group = Adw.PreferencesGroup(
            title=_("Checking your system"),
            description=_(
                "Anything that needs administrator rights is asked once, with the usual "
                "password window. After that, fwmanager never asks again."
            ),
        )
        page.add(self.req_group)
        actions = Adw.PreferencesGroup()
        row = Adw.ActionRow()
        self.recheck_btn = button(_("Check again"))
        self.recheck_btn.connect("clicked", lambda *_: self.refresh_requirements())
        self.req_next = button(_("Continue"), suggested=True)
        self.req_next.set_sensitive(False)
        self.req_next.connect("clicked", lambda *_: self.go("profile"))
        row.add_suffix(self.recheck_btn)
        row.add_suffix(self.req_next)
        actions.add(row)
        page.add(actions)
        return page

    def refresh_requirements(self) -> None:
        self._c.refresh(lambda *_: self.show_requirements())

    def show_requirements(self) -> None:
        snap = self._c.snapshot
        reqs = setup_state.evaluate(
            snap.screen, snap.status, self._group_info(), self._ufw_present(), self._user
        )
        clear(self.req_group, self._rows)
        for req in reqs:
            self._add_requirement(req)
        self.requirements = reqs
        self.req_next.set_sensitive(setup_state.ready(reqs) and not self.helper_running)

    def _add_requirement(self, req: setup_state.Requirement) -> None:
        row = Adw.ActionRow(title=req.title, subtitle=_state_text(req), subtitle_lines=3)
        icon_name, css = STATE_ICON[req.state]
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.add_css_class(css)
        label_for_screen_readers(icon, _state_text(req))
        row.add_prefix(icon)
        row.fix_button = None
        if req.fix == setup_state.FIX_HELPER:
            row.fix_button = button(_("Fix this"), suggested=True)
            row.fix_button.connect("clicked", lambda *_: self.run_helper())
            row.fix_button.set_sensitive(not self.helper_running)
            row.add_suffix(row.fix_button)
        elif req.fix == setup_state.FIX_RESTART:
            row.fix_button = button(_("Restart fwmanager"), suggested=True)
            row.fix_button.connect("clicked", lambda *_: self.restart())
            row.add_suffix(row.fix_button)
        elif req.fix == setup_state.FIX_COMMAND and req.command:
            row.set_subtitle(_("Run this in a terminal, then press “Check again”:"))
            box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
            cmd = Gtk.Label(label=req.command, selectable=True)
            cmd.add_css_class("monospace")
            copy = button("", flat=True, icon="edit-copy-symbolic")
            copy.set_tooltip_text(_("Copy command"))
            label_for_screen_readers(copy, _("Copy command"))
            copy.connect("clicked", lambda *_: self._copy(req.command))
            box.append(cmd)
            box.append(copy)
            row.add_suffix(box)
            row.command_label = cmd
        req_row = row
        req_row.requirement = req
        self.req_group.add(req_row)
        self._rows.append(req_row)

    def _copy(self, text: str) -> None:
        self.get_clipboard().set(text)
        toast(self, _("Command copied"))

    def run_helper(self) -> None:
        """One administrator confirmation (polkit), then everything is checked again."""
        if self.helper_running:
            return
        self.helper_running = True
        self.req_next.set_sensitive(False)
        for row in self._rows:
            if row.fix_button is not None:
                row.fix_button.set_sensitive(False)
        self._run(self._run_helper, self._helper_done)

    def _helper_done(self, result, error) -> None:
        self.helper_running = False
        if error is not None or result is None:
            toast(self, _("Something went wrong: {why}").format(why=_("Internal error.")))
        elif result.ok:
            toast(self, _("Done."))
        elif result.dismissed:
            toast(self, _("Cancelled. Nothing was changed."))
        elif result.not_authorised:
            toast(self, _("Not authorised. Ask an administrator, or use the command below."))
        elif not result.available:
            toast(self, _("The password window is not available. Use the commands shown instead."))
            self._show_manual_commands()
            return
        else:
            toast(self, _("The setup step failed. Try the commands shown instead."))
            self._show_manual_commands()
            return
        self.refresh_requirements()

    def _show_manual_commands(self) -> None:
        """No polkit here: turn the helper rows into copyable commands."""
        for row in list(self._rows):
            req = row.requirement
            if req.fix == setup_state.FIX_HELPER and req.command:
                self.req_group.remove(row)
                self._rows.remove(row)
                self._add_requirement(
                    setup_state.Requirement(req.id, req.state, setup_state.FIX_COMMAND, req.command)
                )

    def restart(self) -> None:
        """Start again with the new group so the service accepts this window."""
        self._on_stage("requirements")
        if not self._relaunch():
            toast(self, _("Please log out and back in, then open fwmanager again."))

    # -- 3. profile for this network ---

    def _profile_page(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()
        self.network_group = Adw.PreferencesGroup(
            title=_("How should this computer behave on this network?"),
            description=_(
                "You can change this at any time, and fwmanager remembers it per network."
            ),
        )
        self.network_row = Adw.ActionRow(title=_("Current network"), subtitle=_("Checking…"))
        self.network_group.add(self.network_row)
        first = None
        for name in CHOICES:
            check = Gtk.CheckButton()
            if first is None:
                first = check
            else:
                check.set_group(first)
            row = Adw.ActionRow(
                title=state.profile_title(name), subtitle=state.profile_hint(name), activatable=True
            )
            row.add_prefix(check)
            row.set_activatable_widget(check)
            check.connect("toggled", lambda *_: self._update_open_warning())
            self._choice_rows[name] = check
            self.network_group.add(row)
        self._choice_rows["publico"].set_active(True)  # the safe default
        self.open_warning = warning_row()
        self.network_group.add(self.open_warning)
        self.remember = Adw.SwitchRow(
            title=_("Remember this choice for this network"), active=True, visible=False
        )
        self.network_group.add(self.remember)
        page.add(self.network_group)

        self.confirm_group = Adw.PreferencesGroup(visible=False)
        self.confirm_row = Adw.ActionRow(title=_("Is your connection still working?"))
        self.keep_btn = button(_("Yes, keep it"), suggested=True)
        self.keep_btn.connect("clicked", lambda *_: self._c.confirm(self._kept))
        self.undo_btn = button(_("No, undo it"))
        self.undo_btn.connect("clicked", lambda *_: self._c.rollback(self._undone))
        self.confirm_row.add_suffix(self.undo_btn)
        self.confirm_row.add_suffix(self.keep_btn)
        self.confirm_group.add(self.confirm_row)
        page.add(self.confirm_group)

        actions = Adw.PreferencesGroup()
        row = Adw.ActionRow()
        self.test_btn = button(_("Test"))
        self.test_btn.connect("clicked", lambda *_: self._test())
        self.apply_btn = button(_("Apply"), suggested=True)
        self.apply_btn.connect("clicked", lambda *_: self._apply())
        self.later_btn = button(_("Decide later"), flat=True)
        self.later_btn.connect("clicked", lambda *_: self.go("done"))
        row.add_suffix(self.later_btn)
        row.add_suffix(self.test_btn)
        row.add_suffix(self.apply_btn)
        actions.add(row)
        page.add(actions)
        return page

    @property
    def choice(self) -> str:
        return next(n for n, c in self._choice_rows.items() if c.get_active())

    def _network_loaded(self, net, _error) -> None:
        self._network = net
        self.network_row.set_subtitle(state.network_text(net))
        self.remember.set_visible(net is not None)
        self._update_open_warning()

    def _update_open_warning(self) -> None:
        if not hasattr(self, "open_warning"):  # the choices are built before the warning row
            return
        chosen = [n for n, c in self._choice_rows.items() if c.get_active()]
        if chosen:  # while a radio group switches, for a moment nothing is active
            show_warning(self.open_warning, state.open_wifi_warning(self._network, chosen[0]))

    def on_snapshot(self, snap) -> None:
        """Keep the countdown honest while a choice waits for confirmation."""
        st = snap.status or {}
        pending = st.get("pending_profile", "")
        self.confirm_group.set_visible(bool(pending) and self.stage == "profile")
        busy = bool(pending or self._c.applying)
        for w in (self.apply_btn, self.test_btn):
            w.set_sensitive(not busy)
        self.apply_btn.set_label(_("Applying…") if self._c.applying else _("Apply"))
        if pending:
            self.confirm_row.set_subtitle(state.countdown_text(pending, st.get("seconds_left", 0)))
        if self.stage == "requirements" and not self.helper_running:
            self.show_requirements()

    def _test(self) -> None:
        dialog = PolicyTestDialog(self._c, self.choice, lambda name: self._apply(name))
        dialog.present(self.get_root())
        dialog.start()

    def _apply(self, name: str | None = None) -> None:
        self._applied = name or self.choice
        self._c.apply(self._applied, self._apply_done)

    def _apply_done(self, _result, error) -> None:
        if error is not None:
            self._applied = None
            toast_error(self, error)

    def _kept(self, _result, error) -> None:
        if error is not None:
            toast_error(self, error)
            return
        net, name = self._network, self._applied
        if net is not None and name and self.remember.get_active():
            self._c.set_network(net.ssid, net.uuid, name, lambda *_: None)
        self.go("done")

    def _undone(self, _result, error) -> None:
        if error is not None:
            toast_error(self, error)
            return
        self._applied = None
        toast(self, _("Undone. Pick another profile, or test it first."))

    # -- 4. done ---

    def _done_page(self) -> Adw.StatusPage:
        page = Adw.StatusPage(
            icon_name="emblem-ok-symbolic",
            title=_("You are all set"),
            description=_(
                "fwmanager switches profile by itself when you change network. Use Test "
                "before applying anything new, and Rules for your own settings."
            ),
        )
        self.finish_btn = button(_("Open fwmanager"), suggested=True)
        self.finish_btn.add_css_class("pill")
        self.finish_btn.set_halign(Gtk.Align.CENTER)
        self.finish_btn.connect("clicked", lambda *_: self.finish())
        page.set_child(self.finish_btn)
        return page
