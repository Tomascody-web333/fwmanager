"""Language selection: user setting > system config > locale, changed live from Settings."""

import pytest
from helpers.fake_client import FakeClient, FakeDaemon
from helpers.gtk_tools import texts
from helpers.shot import pump

from fwmanager import i18n_build as ib
from fwmanager.i18n import set_language
from fwmanager.ui.controller import Controller
from fwmanager.ui.worker import run_sync

pytestmark = pytest.mark.ui


@pytest.fixture
def localedir(tmp_path, monkeypatch):
    out = tmp_path / "locale"
    ib.compile_dir(__import__("pathlib").Path(__file__).resolve().parents[2] / "po", out)
    monkeypatch.setenv("FWMANAGER_LOCALEDIR", str(out))
    monkeypatch.setattr("fwmanager.i18n.DEFAULT_LOCALEDIR", str(out))
    yield out
    set_language(None)


@pytest.fixture
def make_app(gtk, localedir, tmp_path):
    from fwmanager.ui.app import FwApplication

    apps = []

    def make(user_lang=None, system_lang=None, stage="done"):
        daemon = FakeDaemon()
        settings = tmp_path / f"s{len(apps)}" / "settings.toml"
        system = tmp_path / f"c{len(apps)}.toml"
        settings.parent.mkdir(parents=True, exist_ok=True)
        lang_line = f'lang = "{user_lang}"\n' if user_lang else ""
        settings.write_text(lang_line + f'setup_stage = "{stage}"\n')
        if system_lang:
            system.write_text(f'lang = "{system_lang}"\n')
        app = FwApplication(
            lambda: Controller(lambda: FakeClient(daemon), run=run_sync, user="ana"),
            settings_path=settings,
            system_config=system,
        )
        app.register()
        app.activate()
        pump(0.4)
        app.daemon, app.settings_file = daemon, settings
        apps.append(app)
        return app

    yield make
    for app in apps:
        if app.window:
            app.controller.stop_polling()
            app.window.destroy()


def page_titles(app):
    return [page.get_title() for _name, page in app.window.pages]


def test_user_setting_wins(make_app):
    app = make_app(user_lang="pt", system_lang="en")
    assert page_titles(app) == ["Casa", "Perfis", "Redes", "Regras", "Registo", "Definições"]
    assert "Estado" in texts(app.window.home)


def test_system_config_is_used_without_a_user_setting(make_app):
    app = make_app(system_lang="pt")
    assert page_titles(app)[0] == "Casa"


def test_english_when_nothing_says_otherwise(make_app, monkeypatch):
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    app = make_app()
    assert page_titles(app) == ["Home", "Profiles", "Networks", "Rules", "Log", "Settings"]


def test_changing_language_rebuilds_the_window_and_remembers_it(make_app):
    app = make_app(user_lang="pt")
    old_window, controller = app.window, app.controller
    calls_before = len(app.daemon.calls)
    app.window.settings.language.set_selected(1)  # English
    pump(0.4)
    assert app.window is not old_window and app.controller is controller  # same daemon link
    assert page_titles(app) == ["Home", "Profiles", "Networks", "Rules", "Log", "Settings"]
    assert 'lang = "en"' in app.settings_file.read_text()
    assert app.window.stack.get_visible_child_name() == "main"
    # Nothing language-related went to the daemon.
    sent = {c[0] for c in app.daemon.calls[calls_before:]}
    assert not sent & {"set_options", "set_profile", "set_network_profile", "apply_profile"}


def test_changing_back_to_portuguese(make_app):
    app = make_app(user_lang="en")
    app.window.settings.language.set_selected(0)
    pump(0.4)
    assert page_titles(app)[0] == "Casa" and 'lang = "pt"' in app.settings_file.read_text()


def test_selecting_the_current_language_does_nothing(make_app):
    app = make_app(user_lang="pt")
    window = app.window
    app.change_language("pt")
    assert app.window is window


def test_dialogs_and_problem_screens_follow_the_language(make_app):
    from fwmanager.errors import FwError

    app = make_app(user_lang="pt")
    app.daemon.error = FwError("x", code="access_denied")
    app.controller.refresh()
    pump(0.3)
    t = texts(app.window.problem)
    assert "A sua conta não tem permissão para usar o fwmanager" in t
    assert any("sudo usermod -aG fwmanager" in x for x in t)  # commands are never translated


def test_notifications_switch_is_saved(make_app):
    app = make_app()
    app.window.settings.notifications.set_active(False)
    assert "notifications = false" in app.settings_file.read_text()
    assert app.user_settings.notifications is False
