"""The main window: pages, banner, pending-confirmation, and the explanatory problem screen."""

from __future__ import annotations

import getpass

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from fwmanager.i18n import _  # noqa: E402
from fwmanager.ui import state  # noqa: E402
from fwmanager.ui.common import toast_error  # noqa: E402
from fwmanager.ui.dialogs import PolicyTestDialog  # noqa: E402
from fwmanager.ui.pages.home import HomePage  # noqa: E402
from fwmanager.ui.pages.log import LogPage  # noqa: E402
from fwmanager.ui.pages.networks import NetworksPage  # noqa: E402
from fwmanager.ui.pages.problem import ProblemPage  # noqa: E402
from fwmanager.ui.pages.rules import RulesPage  # noqa: E402
from fwmanager.ui.pages.settings import SettingsPage  # noqa: E402
from fwmanager.ui.wizard import SetupWizard  # noqa: E402


class MainWindow(Adw.ApplicationWindow):
    def __init__(
        self,
        app,
        controller,
        user_settings,
        on_language,
        on_notifications,
        on_setup_stage=lambda stage: None,
        wizard_options: dict | None = None,
        uninstall_options: dict | None = None,
    ) -> None:
        super().__init__(
            application=app,
            title="fwmanager",
            default_width=900,
            default_height=680,
            width_request=360,
            height_request=400,
        )
        self.controller = controller
        self._was_ok = False
        self._closing = False  # set once the person has decided about a pending change
        self._on_setup_stage = on_setup_stage
        self._uninstall_options = dict(uninstall_options or {})
        self.toasts = Adw.ToastOverlay()

        # -- pages ---
        self.home = HomePage(controller, self.open_policy_test)
        self.networks = NetworksPage(controller)
        self.rules = RulesPage(controller, self._refresh_profiles)
        self.log = LogPage(controller)
        self.settings = SettingsPage(
            controller, user_settings, on_language, on_notifications, self.open_uninstall
        )
        self.pages = [
            ("home", self.home),
            ("networks", self.networks),
            ("rules", self.rules),
            ("log", self.log),
            ("settings", self.settings),
        ]
        self.views = Adw.ViewStack()
        for name, page in self.pages:
            self.views.add_titled_with_icon(page, name, page.get_title(), page.get_icon_name())
        self.views.connect("notify::visible-child-name", self._on_page_changed)

        # -- chrome ---
        self.switcher = Adw.ViewSwitcher(stack=self.views, policy=Adw.ViewSwitcherPolicy.WIDE)
        header = Adw.HeaderBar(title_widget=self.switcher)
        self.banner = Adw.Banner(revealed=False)
        self.bottom_bar = Adw.ViewSwitcherBar(stack=self.views)
        main = Adw.ToolbarView()
        main.add_top_bar(header)
        main.add_top_bar(self.banner)
        main.set_content(self.views)
        main.add_bottom_bar(self.bottom_bar)

        self.problem = ProblemPage(controller)
        problem_view = Adw.ToolbarView()
        problem_view.add_top_bar(Adw.HeaderBar())
        problem_view.set_content(self.problem)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(main, "main")
        self.stack.add_named(problem_view, "problem")
        loading = Adw.StatusPage(title=_("Connecting to the service…"))
        loading.set_child(Gtk.Spinner(spinning=True, width_request=32, height_request=32))
        self.stack.add_named(loading, "loading")
        self.stack.set_visible_child_name("loading")

        # First run: the setup wizard takes over until it is finished or skipped.
        self.wizard = None
        if user_settings.setup_stage != "done":
            options = dict(wizard_options or {})
            self.wizard = SetupWizard(
                controller,
                user_settings,
                options.pop("user", getpass.getuser()),
                on_language,
                on_setup_stage,
                self._wizard_finished,
                **options,
            )
            self.stack.add_named(self.wizard, "setup")
            self.stack.set_visible_child_name("setup")
        self.toasts.set_child(self.stack)
        self.set_content(self.toasts)

        # Narrow windows: the page switcher moves to the bottom.
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 780sp"))
        bp.add_setter(self.bottom_bar, "reveal", True)
        bp.add_setter(self.switcher, "visible", False)
        self.add_breakpoint(bp)

        self._add_shortcuts()
        controller.listeners.append(self.on_snapshot)
        self.connect("close-request", self._on_close)
        if controller.snapshot.status is not None or controller.snapshot.problem is not None:
            self.on_snapshot(controller.snapshot)  # e.g. a window rebuilt after a language change
        controller.start_polling()

    # -- keyboard: Ctrl+1..5 jumps between pages ---

    def _add_shortcuts(self) -> None:
        ctl = Gtk.ShortcutController()
        for i, (name, _page) in enumerate(self.pages, start=1):
            trigger = Gtk.ShortcutTrigger.parse_string(f"<Control>{i}")
            action = Gtk.CallbackAction.new(
                lambda *_a, n=name: self.views.set_visible_child_name(n)
            )
            ctl.add_shortcut(Gtk.Shortcut.new(trigger, action))
        self.add_controller(ctl)

    # -- reacting to the daemon ---

    def _wizard_finished(self) -> None:
        wizard, self.wizard = self.wizard, None
        self.stack.set_visible_child_name("loading")
        if wizard is not None:
            self.stack.remove(wizard)
        self._was_ok = False
        self.on_snapshot(self.controller.snapshot)

    def on_snapshot(self, snap) -> None:
        if self.wizard is not None and self._set_up_elsewhere(snap):
            self._on_setup_stage("done")  # e.g. `fwmanager-cli setup`: nothing left to do here
            self._wizard_finished()
            return
        if self.wizard is not None:
            self.wizard.on_snapshot(snap)  # setup explains problems itself, in its own steps
            return
        if snap.screen != state.OK:
            self._was_ok = False
            if snap.problem:
                self.problem.show(snap.problem)
            self.stack.set_visible_child_name("problem")
            return
        self.stack.set_visible_child_name("main")
        if not self._was_ok:  # first time (or back after a problem): load everything
            self._was_ok = True
            self.home.load()
            self.rules.load()
            self.settings.load()
            self.networks.load()
            self.controller.current_network(self._network_loaded)
        self.home.on_snapshot(snap)
        self._update_banner(snap.status or {})

    def _set_up_elsewhere(self, snap) -> bool:
        """Everything works and a profile was already chosen, and setup has not been started."""
        return (
            snap.screen == state.OK
            and bool((snap.status or {}).get("configured"))
            and self.wizard.stage in ("welcome", "requirements")
        )

    def _refresh_profiles(self) -> None:
        """Rules change what profiles allow: redraw the pages that describe them."""
        self.home.load()

    def _network_loaded(self, net, _error) -> None:
        self.home.set_current_network(net)

    def _update_banner(self, status: dict) -> None:
        text = ""
        if status.get("startup") == "state_corrupt":
            text = _("The saved settings were damaged, so the strictest profile was used.")
        elif status.get("warnings"):
            text = _(
                "Docker or virtual machines were found. They manage their own rules, "
                "which fwmanager leaves alone."
            )
        self.banner.set_title(text)
        self.banner.set_revealed(bool(text))

    def _on_page_changed(self, *_args) -> None:
        name = self.views.get_visible_child_name()
        if name == "log":
            self.log.load()
        elif name == "networks":
            self.networks.load()
        elif name == "rules":
            self.rules.load()

    def _on_close(self, *_args) -> bool:
        pending = (self.controller.snapshot.status or {}).get("pending_profile", "")
        if pending and not self._closing:
            self.ask_before_closing(pending)
            return True  # stay open until they choose
        self.detach()
        return False

    def detach(self) -> None:
        """Stop following the service: a window that is gone must not be updated."""
        if self.on_snapshot in self.controller.listeners:
            self.controller.listeners.remove(self.on_snapshot)

    def ask_before_closing(self, pending: str) -> None:
        """Closing is easy to mistake for "done": say that the change is not kept yet."""
        dialog = Adw.AlertDialog(
            heading=_("Keep the new profile?"),
            body=_(
                "{profile} is still waiting for your confirmation. If you close without "
                "choosing, it is undone in a few seconds."
            ).format(profile=state.profile_title(pending)),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("undo", _("Undo it"))
        dialog.add_response("keep", _("Keep it"))
        dialog.set_response_appearance("keep", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("keep")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._close_response)
        self.close_dialog = dialog
        dialog.present(self)

    def _close_response(self, _dialog, response: str) -> None:
        if response == "keep":
            self.controller.confirm(self._close_after)
        elif response == "undo":
            self.controller.rollback(self._close_after)

    def _close_after(self, _result, error) -> None:
        if error is not None and error.code != "no_pending":  # already undone is fine
            toast_error(self, error)
            return
        self._closing = True
        GLib.idle_add(self.close)  # after the question has gone away, or it would catch the close

    # -- dialogs ---

    def open_uninstall(self):
        from fwmanager.ui.uninstall import UninstallDialog

        options = {"on_busy": self._uninstalling, **self._uninstall_options}
        dialog = UninstallDialog(self._removed, **options)
        dialog.present(self)
        self.uninstall_dialog = dialog
        return dialog

    def _uninstalling(self, busy: bool) -> None:
        """While the service is being removed, its absence is expected: no error screen."""
        if busy:
            self.detach()
            self.controller.stop_polling()
        elif self.on_snapshot not in self.controller.listeners:
            self.controller.listeners.append(self.on_snapshot)
            self.controller.start_polling()

    def _removed(self) -> None:
        """fwmanager is gone from the machine: there is nothing left to show."""
        self._closing = True
        dialog = getattr(self, "uninstall_dialog", None)
        if dialog is not None:
            dialog.force_close()  # an open dialog would catch the window's close
        app = self.get_application()
        self.close()
        if app is not None:
            app.quit()

    def open_policy_test(self, profile: str) -> None:
        dialog = PolicyTestDialog(self.controller, profile, self._apply_after_test)
        dialog.present(self)
        dialog.start()

    def _apply_after_test(self, profile: str) -> None:
        self.controller.apply(profile)


__all__ = ["MainWindow", "Gdk"]
