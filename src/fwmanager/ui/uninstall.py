"""Removing fwmanager from the window: say what will happen, ask once, then say it is done.

The work is the same as ``fwmanager-cli uninstall``: one privileged helper run (polkit asks for
an administrator password) that undoes fwmanager's changes to ufw and removes the window, the
command line and the service for every account. Whichever is used, there is nothing left for
the other to do.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from fwmanager.i18n import _  # noqa: E402
from fwmanager.ui import privileged  # noqa: E402
from fwmanager.ui.common import button, show_warning, warning_row  # noqa: E402
from fwmanager.ui.worker import run_async  # noqa: E402

MANUAL_COMMAND = "sudo /usr/local/libexec/fwmanager/uninstall"


def _run_uninstall_helper() -> privileged.HelperResult:
    return privileged.run_setup_helper(verb="uninstall")


class UninstallDialog(Adw.Dialog):
    def __init__(self, on_removed, run_helper=_run_uninstall_helper, run=run_async) -> None:
        super().__init__(title=_("Uninstall fwmanager"), content_width=460, content_height=520)
        self._on_removed, self._run_helper, self._run = on_removed, run_helper, run
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self._ask_page(), "ask")
        self.stack.add_named(self._working_page(), "working")
        self.stack.add_named(self._done_page(), "done")
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(self.stack)
        self.set_child(view)

    # -- pages ---

    def _ask_page(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()
        what = Adw.PreferencesGroup(
            title=_("What happens"),
            description=_("Your firewall is left the way it was before fwmanager."),
        )
        for text in (
            _("fwmanager's own rules and its ping/mDNS settings are removed."),
            _("ufw's original default settings are put back."),
            _("ufw is switched off only if fwmanager was the one that switched it on."),
            _("Rules you added to ufw yourself are kept."),
            _("The window, the command line and the background service are removed, "
              "for every account on this computer."),
        ):  # fmt: skip
            row = Adw.ActionRow(title=text, title_lines=0)
            row.add_prefix(Gtk.Image.new_from_icon_name("emblem-ok-symbolic"))
            what.add(row)
        page.add(what)

        actions = Adw.PreferencesGroup()
        self.problem = warning_row()
        actions.add(self.problem)
        self.command_row = Adw.ActionRow(
            title=MANUAL_COMMAND, subtitle=_("Or run this in a terminal:"), visible=False
        )
        self.command_row.add_css_class("monospace")
        actions.add(self.command_row)
        row = Adw.ActionRow(
            title=_("An administrator password is asked once."), title_lines=0
        )  # fmt: skip
        self.cancel_btn = button(_("Cancel"))
        self.cancel_btn.connect("clicked", lambda *_: self.close())
        self.uninstall_btn = button(_("Uninstall"))
        self.uninstall_btn.add_css_class("destructive-action")
        self.uninstall_btn.connect("clicked", lambda *_: self.start())
        row.add_suffix(self.cancel_btn)
        row.add_suffix(self.uninstall_btn)
        actions.add(row)
        page.add(actions)
        return page

    def _working_page(self) -> Gtk.Widget:
        status = Adw.StatusPage(
            title=_("Removing fwmanager…"),
            description=_("Confirm with the administrator password when asked."),
        )
        status.set_child(Gtk.Spinner(spinning=True, width_request=32, height_request=32))
        return status

    def _done_page(self) -> Gtk.Widget:
        status = Adw.StatusPage(
            icon_name="emblem-ok-symbolic",
            title=_("fwmanager was removed"),
            description=_("Your firewall is back to how it was before fwmanager. Thank you!"),
        )
        self.close_btn = button(_("Close"), suggested=True)
        self.close_btn.set_halign(Gtk.Align.CENTER)
        self.close_btn.add_css_class("pill")
        self.close_btn.connect("clicked", lambda *_: self._on_removed())
        status.set_child(self.close_btn)
        return status

    # -- the work ---

    def start(self) -> None:
        self.set_can_close(False)  # half-way is not a moment to walk away
        self.stack.set_visible_child_name("working")
        self._run(self._run_helper, self._finished)

    def _finished(self, result, error) -> None:
        self.set_can_close(True)
        if error is None and result is not None and result.ok:
            self.stack.set_visible_child_name("done")
            return
        self.stack.set_visible_child_name("ask")
        if error is not None or result is None:
            text = _("Something went wrong: {why}").format(why=_("Internal error."))
        elif result.dismissed:
            text = _("Cancelled. Nothing was changed.")
        elif result.not_authorised:
            text = _("Not authorised. Ask an administrator, or use the command below.")
        elif not result.available:
            text = _("The password window is not available. Use the command below.")
        else:
            text = _("Uninstalling failed. Use the command below to see why.")
        show_warning(self.problem, text)
        self.command_row.set_visible(not (result is not None and result.dismissed))
