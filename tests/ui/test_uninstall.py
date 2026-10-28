"""Uninstalling from Settings, and setup done elsewhere (e.g. the CLI) not being asked again."""

import pytest
from helpers.fake_client import FakeClient, FakeDaemon
from helpers.gtk_tools import texts
from helpers.shot import pump
from test_wizard import Env

from fwmanager.i18n import set_language
from fwmanager.ui import privileged
from fwmanager.ui.controller import Controller
from fwmanager.ui.worker import run_sync

pytestmark = pytest.mark.ui


@pytest.fixture(autouse=True)
def english():
    set_language(None)
    yield
    set_language(None)


@pytest.fixture
def win(gtk):
    from gi.repository import Adw

    from fwmanager.ui import settings as us
    from fwmanager.ui.window import MainWindow

    daemon = FakeDaemon()
    controller = Controller(lambda: FakeClient(daemon), run=run_sync, user="ana")
    app = Adw.Application(application_id="org.fwmanager.TestUninstall")
    app.register()
    calls, results = [], []

    def helper():
        calls.append("uninstall")
        return results.pop(0) if results else privileged.HelperResult(0)

    w = MainWindow(
        app, controller, us.UserSettings(setup_stage="done"), print, print,
        uninstall_options=dict(run_helper=helper, run=run_sync),
    )  # fmt: skip
    w.present()
    pump(0.3)
    w.helper_calls, w.helper_results, w.daemon = calls, results, daemon
    yield w
    controller.stop_polling()
    w.destroy()


def test_settings_has_an_uninstall_button_that_explains_first(win):
    win.views.set_visible_child_name("settings")
    assert "Uninstall fwmanager" in texts(win.settings)
    win.settings.uninstall_btn.emit("clicked")
    pump(0.2)
    d = win.uninstall_dialog
    t = texts(d)
    assert d.stack.get_visible_child_name() == "ask" and win.helper_calls == []  # asked first
    assert "Rules you added to ufw yourself are kept." in t
    assert "ufw is switched off only if fwmanager was the one that switched it on." in t


def test_uninstalling_ends_on_a_done_page_and_closes_the_app(win):
    d = win.open_uninstall()
    d.uninstall_btn.emit("clicked")
    pump(0.2)
    assert win.helper_calls == ["uninstall"] and d.stack.get_visible_child_name() == "done"
    assert "fwmanager was removed" in texts(d)
    d.close_btn.emit("clicked")
    pump(0.2)
    assert not win.get_visible()


@pytest.mark.parametrize(
    "code,message,command",
    [
        (privileged.DISMISSED, "Cancelled. Nothing was changed.", False),
        (privileged.NOT_AUTHORISED, "Not authorised.", True),
        (-1, "The password window is not available.", True),
        (1, "Uninstalling failed.", True),
    ],
)
def test_a_helper_that_did_not_finish_leaves_everything_and_says_why(win, code, message, command):
    win.helper_results.append(privileged.HelperResult(code))
    d = win.open_uninstall()
    d.uninstall_btn.emit("clicked")
    pump(0.2)
    assert d.stack.get_visible_child_name() == "ask" and win.get_visible()
    assert d.problem.get_visible() and message in d.problem.get_title()
    assert d.command_row.get_visible() is command
    assert d.get_can_close()


# -- setup done elsewhere ---


def test_setup_done_in_the_cli_skips_the_wizard(gtk):
    e = Env(gtk, stage="welcome")
    try:
        e.daemon.configured = True  # `fwmanager-cli setup` chose and confirmed a profile
        e.controller.refresh()
        pump(0.2)
        assert e.win.wizard is None and e.win.stack.get_visible_child_name() == "main"
        assert e.stages[-1] == "done"  # remembered: never shown again
    finally:
        e.close()


def test_setup_not_done_anywhere_still_shows_the_wizard(gtk):
    e = Env(gtk, stage="welcome")
    try:
        e.controller.refresh()
        pump(0.2)
        assert e.win.wizard is not None and e.win.stack.get_visible_child_name() == "setup"
    finally:
        e.close()


def test_a_wizard_already_choosing_a_profile_is_not_taken_away(gtk):
    e = Env(gtk, stage="profile")
    try:
        e.daemon.configured = True
        e.controller.refresh()
        pump(0.2)
        assert e.win.wizard is not None
    finally:
        e.close()
