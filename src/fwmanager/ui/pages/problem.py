"""The explanatory screen shown when the service is missing, not allowed, or in conflict."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from fwmanager.i18n import _  # noqa: E402
from fwmanager.ui import state  # noqa: E402
from fwmanager.ui.common import button, label_for_screen_readers, toast  # noqa: E402

ICONS = {
    state.DAEMON_DOWN: "network-error-symbolic",
    state.NOT_IN_GROUP: "dialog-password-symbolic",
    state.UFW_MISSING: "package-x-generic-symbolic",
    state.CONFLICT: "dialog-warning-symbolic",
}


class ProblemPage(Adw.Bin):
    def __init__(self, controller) -> None:
        super().__init__()
        self._c = controller
        self.page = Adw.StatusPage()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER)
        self.command = Gtk.Label(selectable=True, wrap=True, xalign=0.5)
        self.command.add_css_class("monospace")
        frame = Gtk.Frame()
        frame.set_child(self.command)
        self.command.set_margin_top(10)
        self.command.set_margin_bottom(10)
        self.command.set_margin_start(14)
        self.command.set_margin_end(14)
        self.copy_btn = button("", icon="edit-copy-symbolic")
        self.copy_btn.set_tooltip_text(_("Copy command"))
        label_for_screen_readers(self.copy_btn, _("Copy command"))
        self.copy_btn.connect("clicked", self._copy)
        self.retry_btn = button(_("Try again"), suggested=True)
        self.retry_btn.connect("clicked", lambda *_: self._c.refresh())
        actions = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        actions.append(self.copy_btn)
        actions.append(self.retry_btn)
        box.append(frame)
        box.append(actions)
        self.page.set_child(box)
        self.set_child(self.page)
        label_for_screen_readers(self.command, _("Command to run"))

    def show(self, problem: state.Problem) -> None:
        self.page.set_icon_name(ICONS.get(problem.screen, "dialog-error-symbolic"))
        self.page.set_title(problem.title)
        self.page.set_description(problem.body)
        self.command.set_text(problem.command or "")
        self.command.get_parent().set_visible(bool(problem.command))
        self.copy_btn.set_visible(bool(problem.command))
        self.command.select_region(0, 0)  # no highlighted text when the screen opens
        self.retry_btn.grab_focus()

    def _copy(self, *_args) -> None:
        self.get_clipboard().set(self.command.get_text())
        toast(self, _("Command copied"))
