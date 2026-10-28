"""Notifications with the window closed: fwmanager keeps running and starts at login."""

import pytest
from helpers.shot import pump
from test_language import localedir, make_app  # noqa: F401

from fwmanager.ui import settings as us  # no GTK in this module

pytestmark = pytest.mark.ui


def autostart(app):
    return app.settings_file.parent.parent / "autostart" / us.AUTOSTART_NAME


def test_closing_the_window_keeps_the_app_for_notifications(make_app):  # noqa: F811
    app = make_app(user_lang="en")
    assert app.held  # on by default
    app.window.close()
    pump(0.2)
    assert app.window is None
    app.daemon.signal_cb("Confirmed", ("home",))  # e.g. the network changed meanwhile
    assert app.sent == [("switched", "Now using the Home profile.")]
    app.activate()  # opening fwmanager again shows a window in this same instance
    pump(0.2)
    assert app.window is not None


def test_it_starts_at_login_once_set_up(make_app):  # noqa: F811
    app = make_app(user_lang="en")
    entry = autostart(app).read_text()
    assert "Exec=fwmanager --background" in entry and "TryExec=fwmanager" in entry
    assert "NoDisplay=true" in entry


def test_not_before_setup_is_done(make_app):  # noqa: F811
    app = make_app(user_lang="en", stage="welcome")
    assert not autostart(app).exists()
    app.set_setup_stage("done")
    assert autostart(app).exists()


def test_switching_it_off_lets_the_app_end_with_its_window(make_app):  # noqa: F811
    app = make_app(user_lang="en")
    app.window.settings.background.set_active(False)
    assert not app.held and not autostart(app).exists()
    assert "background = false" in app.settings_file.read_text()
    app.window.settings.background.set_active(True)
    assert app.held and autostart(app).exists()


def test_started_at_login_it_shows_no_window(gtk, localedir, tmp_path):  # noqa: F811
    from helpers.fake_client import FakeClient, FakeDaemon

    from fwmanager.ui.app import FwApplication
    from fwmanager.ui.controller import Controller
    from fwmanager.ui.worker import run_sync

    settings = tmp_path / "cfg" / "fwmanager" / "settings.toml"
    settings.parent.mkdir(parents=True)
    settings.write_text('lang = "en"\nsetup_stage = "done"\n')
    daemon = FakeDaemon()
    app = FwApplication(
        lambda: Controller(lambda: FakeClient(daemon), run=run_sync, user="ana"),
        settings_path=settings,
        background=True,
    )
    app.register()
    app.activate()
    pump(0.3)
    try:
        assert app.window is None and app.held
        daemon.signal_cb("Reverted", ("work",))
        assert app.sent == [("reverted", "Work was undone because it was not confirmed in time.")]
        app.activate()  # the person opens fwmanager from the menu
        pump(0.2)
        assert app.window is not None
    finally:
        if app.window:
            app.controller.stop_polling()
            app.window.destroy()


def test_removing_fwmanager_removes_the_login_entry(make_app):  # noqa: F811
    app = make_app(user_lang="en")
    assert autostart(app).exists()
    app.forget_login_start()
    assert not autostart(app).exists()


def test_a_router_change_is_announced(make_app):  # noqa: F811
    app = make_app(user_lang="en")
    app.daemon.signal_cb("RouterChanged", ("Casa WiFi", ""))
    assert app.sent == [(
        "router",
        "Casa WiFi has a router fwmanager has not seen there before, so Public is in use. If "
        "you replaced the router, trust it in Networks.",
    )]  # fmt: skip
    app.set_notifications("router", False)
    app.daemon.signal_cb("RouterChanged", ("Casa WiFi", ""))
    assert len(app.sent) == 1
