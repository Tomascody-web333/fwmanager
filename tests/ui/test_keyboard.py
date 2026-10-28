"""Real key presses (xdotool on the virtual display), not calls into the widgets."""

import shutil
import subprocess

import pytest
from helpers.fake_client import FakeClient, FakeDaemon
from helpers.shot import pump

from fwmanager.errors import FwError
from fwmanager.ui.controller import Controller
from fwmanager.ui.worker import run_sync

pytestmark = pytest.mark.ui

PAGES = ("home", "networks", "rules", "log", "settings")


def xdotool(*args):
    return subprocess.run(["xdotool", *args], capture_output=True, text=True).stdout.strip()


@pytest.fixture
def focused(gtk):
    if not shutil.which("xdotool"):
        pytest.skip("xdotool is not installed")
    from gi.repository import Adw

    from fwmanager.ui import settings as us
    from fwmanager.ui.window import MainWindow

    daemon = FakeDaemon()
    controller = Controller(lambda: FakeClient(daemon), run=run_sync, user="ana")
    app = Adw.Application(application_id="org.fwmanager.TestKeys")
    app.register()
    win = MainWindow(
        app, controller, us.UserSettings(setup_stage="done"), lambda c: None, lambda k, b: None
    )
    win.present()
    pump(0.6)
    wid = xdotool("search", "--name", "fwmanager").split("\n")[0]
    xdotool("windowfocus", wid)
    pump(0.3)

    def press(key):
        xdotool("key", "--window", wid, key)
        pump(0.35)

    class Env:
        pass

    e = Env()
    e.win, e.daemon, e.controller, e.press = win, daemon, controller, press
    yield e
    controller.stop_polling()
    win.destroy()


@pytest.mark.parametrize("number,page", list(enumerate(PAGES, start=1)))
def test_ctrl_number_opens_each_page(focused, number, page):
    focused.press(f"ctrl+{number}")
    assert focused.win.views.get_visible_child_name() == page


def test_shortcuts_work_from_any_page_and_in_any_order(focused):
    for key, page in (("ctrl+4", "log"), ("ctrl+2", "networks"), ("ctrl+5", "settings"),
                      ("ctrl+1", "home")):  # fmt: skip
        focused.press(key)
        assert focused.win.views.get_visible_child_name() == page


def test_unrelated_keys_change_nothing(focused):
    focused.press("ctrl+2")
    for key in ("ctrl+6", "ctrl+0", "alt+3", "3"):
        focused.press(key)
    assert focused.win.views.get_visible_child_name() == "networks"


def test_the_problem_screen_can_be_recovered_from_with_the_keyboard_alone(focused):
    focused.daemon.error = FwError("x", code="daemon_unavailable")
    focused.controller.refresh()
    pump(0.4)
    assert focused.win.stack.get_visible_child_name() == "problem"
    assert focused.win.problem.retry_btn.has_focus()  # focus is already on "Try again"
    focused.daemon.error = None  # the user ran the command shown on screen
    focused.press("Return")
    assert focused.win.stack.get_visible_child_name() == "main"


def test_tab_moves_through_the_controls(focused):
    start = focused.win.get_focus()
    focused.press("Tab")
    focused.press("Tab")
    assert focused.win.get_focus() is not start


def test_closing_with_a_pending_change_and_pressing_enter_keeps_it(focused):
    focused.daemon.pending = "home"
    focused.controller.refresh()
    pump(0.2)
    focused.win.close()
    pump(0.4)
    assert focused.win.get_visible()  # it asked first
    focused.press("Return")  # the default answer: keep it
    pump(0.4)
    assert ("confirm", ()) in focused.daemon.calls and focused.daemon.pending == ""
    assert not focused.win.get_visible()


def test_escape_on_that_question_keeps_the_window_open(focused):
    focused.daemon.pending = "home"
    focused.controller.refresh()
    pump(0.2)
    focused.win.close()
    pump(0.4)
    focused.press("Escape")
    pump(0.3)
    assert focused.win.get_visible() and focused.daemon.pending == "home"
