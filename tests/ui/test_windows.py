"""Windows and dialogs: what is shown, and what each control sends to the daemon."""

import pytest
from helpers.fake_client import FakeClient, FakeDaemon
from helpers.gtk_tools import find, texts
from helpers.shot import pump

from fwmanager.errors import FwError
from fwmanager.i18n import set_language
from fwmanager.ui.controller import Controller
from fwmanager.ui.worker import run_sync

pytestmark = pytest.mark.ui


@pytest.fixture(autouse=True)
def english():
    set_language(None)
    yield
    set_language(None)


@pytest.fixture
def daemon():
    return FakeDaemon()


@pytest.fixture
def env(gtk, daemon, tmp_path):
    from gi.repository import Adw

    from fwmanager.ui import settings as us
    from fwmanager.ui.window import MainWindow

    controller = Controller(lambda: FakeClient(daemon), run=run_sync, user="ana")
    app = Adw.Application(application_id="org.fwmanager.Test")
    app.register()
    changes = {"lang": [], "notifications": []}
    win = MainWindow(
        app,
        controller,
        us.UserSettings(setup_stage="done"),
        changes["lang"].append,
        changes["notifications"].append,
    )
    win.present()
    pump(0.3)

    class Env:
        pass

    e = Env()
    e.win, e.controller, e.daemon, e.app, e.changes = win, controller, daemon, app, changes
    yield e
    controller.stop_polling()
    win.destroy()


def visible_child(env):
    return env.win.stack.get_visible_child_name()


def test_home_lists_the_profiles_and_the_active_one(env):
    assert visible_child(env) == "main"
    t = texts(env.win.home)
    for expected in ("Home", "Work", "Public", "Custom", "Status", "Profiles"):
        assert expected in t, t
    assert "Protected with the Public profile" in t
    assert "Lets nothing in. Hidden on the network." in t  # what that means, in plain words
    rows = {r.name_: r for r in env.win.home._profile_rows}
    assert rows["publico"].check.get_opacity() == 1 and rows["casa"].check.get_opacity() == 0
    # The profile in use offers no button to switch to itself.
    assert not rows["publico"].apply_btn.get_visible() and rows["publico"].in_use.get_visible()
    assert rows["casa"].apply_btn.get_visible() and not rows["casa"].in_use.get_visible()


def test_the_status_says_when_the_firewall_is_off(env):
    env.daemon.firewall_active = False
    env.controller.refresh()
    t = texts(env.win.home)
    assert "The firewall is off" in t and "Choose a profile below to turn it on." in t


def test_apply_goes_to_the_daemon_and_shows_the_pending_card(env):
    row = {r.name_: r for r in env.win.home._profile_rows}["casa"]
    row.apply_btn.emit("clicked")
    pump(0.1)
    assert ("apply_profile", ("casa",)) in env.daemon.calls
    assert env.win.home.pending_group.get_visible()
    assert "Home will be undone in 30 s" in env.win.home.pending_row.get_subtitle()
    # While something is pending nothing else can be applied or tested.
    assert not row.apply_btn.get_sensitive() and not row.test_btn.get_sensitive()


def test_confirm_and_undo_buttons(env):
    env.daemon.pending = "casa"
    env.controller.refresh()
    env.win.home.confirm_btn.emit("clicked")
    assert ("confirm", ()) in env.daemon.calls and env.daemon.active == "casa"
    assert not env.win.home.pending_group.get_visible()
    env.daemon.pending = "trabalho"
    env.controller.refresh()
    env.win.home.undo_btn.emit("clicked")
    assert ("rollback", ()) in env.daemon.calls and not env.win.home.pending_group.get_visible()


@pytest.mark.parametrize(
    "setup,title,command",
    [
        (lambda d: setattr(d, "error", FwError("x", code="daemon_unavailable")),
         "The firewall service is not running", "systemctl enable --now fwmanagerd"),
        (lambda d: setattr(d, "error", FwError("x", code="access_denied")),
         "Your account is not allowed to use fwmanager", "usermod -aG fwmanager ana"),
        (lambda d: setattr(d, "ufw_installed", False),
         "ufw is not installed", "sudo apt install ufw"),
        (lambda d: setattr(d, "conflicts", ["firewalld"]),
         "Another firewall manager is active", "systemctl disable --now firewalld.service"),
    ],
)  # fmt: skip
def test_problem_screens(env, setup, title, command):
    setup(env.daemon)
    env.controller.refresh()
    pump(0.2)
    assert visible_child(env) == "problem"
    t = texts(env.win.problem)
    assert title in t and any(command in x for x in t)
    # fixing it and pressing "Try again" brings the normal window back
    env.daemon.error, env.daemon.ufw_installed, env.daemon.conflicts = None, True, []
    env.win.problem.retry_btn.emit("clicked")
    assert visible_child(env) == "main"


def test_banner_for_docker_and_damaged_state(env):
    env.daemon.warnings = ["docker"]
    env.controller.refresh()
    assert env.win.banner.get_revealed() and "Docker" in env.win.banner.get_title()
    env.daemon.warnings, env.daemon.startup = [], "state_corrupt"
    env.controller.refresh()
    assert "damaged" in env.win.banner.get_title()
    env.daemon.startup = "ok"
    env.controller.refresh()
    assert not env.win.banner.get_revealed()


def test_pages_can_be_switched(env):
    assert [name for name, _page in env.win.pages] == [
        "home", "networks", "rules", "log", "settings"]  # fmt: skip
    for name in ("home", "networks", "rules", "log", "settings"):
        env.win.views.set_visible_child_name(name)
        assert env.win.views.get_visible_child_name() == name


def test_narrow_window_moves_the_switcher_to_the_bottom(env):
    env.win.set_default_size(420, 700)
    env.win.set_size_request(420, 700)
    pump(0.6)
    assert env.win.bottom_bar.get_reveal() and not env.win.switcher.get_visible()


# -- the test-policy dialog ---


def outcome(verdict, findings=(), live=None):
    from fwmanager.core.policy_check import Finding
    from fwmanager.policytest import PolicyOutcome

    return PolicyOutcome("casa", [Finding(*f) for f in findings], live, verdict)


def open_dialog(env, result):
    from fwmanager.ui.dialogs import PolicyTestDialog

    applied = []
    dialog = PolicyTestDialog(env.controller, "casa", applied.append)
    dialog.present(env.win)
    dialog.show_outcome(result)
    pump(0.2)
    dialog.applied = applied
    return dialog


def test_safe_verdict_allows_applying(env):
    d = open_dialog(env, outcome("safe", [("info", "ssh_ok", "x")]))
    t = texts(d)
    assert "Safe to use" in t and "Your SSH session and new SSH connections stay allowed." in t
    assert d.apply_btn.get_sensitive() and d.apply_btn.get_label() == "Apply"
    d.apply_btn.emit("clicked")
    assert d.applied == ["casa"]


def test_warnings_allow_applying_anyway(env):
    d = open_dialog(env, outcome("warnings", [("warning", "exposed_to_any", "x")]))
    assert "Safe, with warnings" in texts(d)
    assert d.apply_btn.get_sensitive() and d.apply_btn.get_label() == "Apply anyway"


def test_unsafe_verdict_blocks_applying(env):
    d = open_dialog(env, outcome("unsafe", [("error", "missing_loopback", "x")]))
    t = texts(d)
    assert "Would break something" in t and "Local (loopback) traffic would be blocked." in t
    assert not d.apply_btn.get_sensitive()
    d.apply_btn.emit("clicked")  # even a forced click must not apply it
    assert d.applied == []


def test_live_results_show_before_and_after(env):
    from fwmanager.policytest import LiveResult, Probe

    live = LiveResult(
        [Probe("dns", True), Probe("gateway_v6", None)],
        [Probe("dns", False), Probe("gateway_v6", None)],
        True,
    )
    d = open_dialog(env, outcome("unsafe", [], live))
    t = texts(d)
    assert "DNS resolver answers" in t and "works → fails" in t
    assert "not applicable → not applicable" in t


def test_dialog_runs_the_real_check_through_the_controller(env, monkeypatch):
    from fwmanager import policytest as pt
    from fwmanager.ui.dialogs import PolicyTestDialog

    monkeypatch.setattr(pt, "run_probes", lambda: [pt.Probe("loopback_v4", True)])
    d = PolicyTestDialog(env.controller, "trabalho", lambda n: None)
    d.present(env.win)
    d.start()
    pump(0.3)
    assert d.outcome is not None and d.outcome.verdict == "safe"
    assert ("apply_profile", ("trabalho",)) in env.daemon.calls  # the live test applied it...
    assert ("rollback", ()) in env.daemon.calls and env.daemon.pending == ""  # ...and undid it
    assert d.stack.get_visible_child_name() == "result"


def test_test_button_opens_the_dialog(env, monkeypatch):
    from fwmanager import policytest as pt

    monkeypatch.setattr(pt, "run_probes", lambda: [pt.Probe("loopback_v4", True)])
    row = {r.name_: r for r in env.win.home._profile_rows}["trabalho"]
    row.test_btn.emit("clicked")
    pump(0.4)
    assert ("check_profile", ("trabalho",)) in env.daemon.calls


# -- profile editor ---


def editor(env, name="trabalho"):
    from fwmanager.ui.dialogs import ProfileEditor

    saved = []
    details = env.controller._client.get_profile(name)
    titles = dict(FakeClient().list_services())
    dialog = ProfileEditor(env.controller, details, titles, lambda: saved.append(1))
    dialog.present(env.win)
    pump(0.2)
    dialog.saved = saved
    return dialog


def test_editor_shows_the_current_settings(env):
    d = editor(env)
    assert d._service_rows["ssh"].get_active() and not d._service_rows["web"].get_active()
    assert d.ping.get_active() and not d.mdns.get_active()
    assert not d.cidrs.get_visible()  # only for "specific networks"


def test_editor_saves_what_was_changed(env):
    d = editor(env)
    d._service_rows["web"].set_active(True)
    d.mdns.set_active(True)
    d.source.set_selected(2)  # specific networks
    assert d.cidrs.get_visible()
    d.cidrs.set_text("10.1.0.0/16, 192.168.5.0/24")
    d.save_btn.emit("clicked")
    pump(0.1)
    call = [c for c in env.daemon.calls if c[0] == "set_profile"][-1][1]
    assert call == (
        "trabalho", ["ssh", "web"], True, True, "cidrs", ["10.1.0.0/16", "192.168.5.0/24"]
    )  # fmt: skip
    assert d.saved == [1]


def test_editor_errors_stay_in_the_dialog(env):
    d = editor(env)
    env.daemon.error = FwError("bad", code="invalid_cidr")
    d.save_btn.emit("clicked")
    assert d.saved == [] and d.save_btn.get_sensitive()


def test_editor_reset(env):
    env.daemon.profiles["trabalho"]["customized"] = True
    d = editor(env)
    d.reset_btn.emit("clicked")
    assert ("reset_profile", ("trabalho",)) in env.daemon.calls and d.saved == [1]


def test_profiles_are_edited_from_the_overview(env):
    env.daemon.profiles["casa"]["customized"] = True
    env.win.home.load()
    row = {r.name_: r for r in env.win.home._profile_rows}["casa"]
    assert "You edited this profile." in row.get_subtitle()
    row.edit_btn.emit("clicked")
    pump(0.2)
    dialog = env.win.get_visible_dialog()
    assert dialog is not None and dialog.__class__.__name__ == "ProfileEditor"
    dialog.close()


# -- networks, log, settings ---


def test_networks_page_lists_and_changes_remembered_networks(env):
    env.win.networks.load()
    assert "Casa WiFi" in texts(env.win.networks)
    uuid = env.daemon.networks[0][1]
    row = env.win.networks._rows[0]
    drop = find(row, lambda w: w.__class__.__name__ == "DropDown")[0]
    drop.set_selected(1)  # Work
    assert ("set_network_profile", ("Casa WiFi", uuid, "trabalho")) in env.daemon.calls
    remove = find(row, lambda w: w.__class__.__name__ == "Button")[0]
    remove.emit("clicked")
    assert ("remove_network", ("Casa WiFi", uuid)) in env.daemon.calls
    assert env.win.networks._rows == [] or "Casa WiFi" not in texts(env.win.networks)


def test_networks_page_remembers_the_current_network(env):
    from fwmanager.ui.netinfo import CurrentNetwork

    net = CurrentNetwork("Cafe auto", "Café", "u-1", "802-11-wireless")
    env.win.networks._current_loaded(net, None)
    assert env.win.networks.current_group.get_visible()
    env.win.networks.current_row.set_selected(2)  # Public
    env.win.networks.remember_btn.emit("clicked")
    assert ("set_network_profile", ("Café", "u-1", "publico")) in env.daemon.calls


def test_networks_page_preselects_the_remembered_profile(env):
    from fwmanager.ui.netinfo import CurrentNetwork

    uuid = env.daemon.networks[0][1]
    env.win.networks.load()
    env.win.networks._current_loaded(
        CurrentNetwork("x", "Casa WiFi", uuid, "802-11-wireless"), None
    )
    assert env.win.networks.current_row.get_selected() == 0  # Home, as remembered


def test_networks_page_adds_a_network_by_name(env):
    env.win.networks.ssid_entry.set_text("Biblioteca")
    env.win.networks.add_profile.set_selected(2)
    env.win.networks.add_btn.emit("clicked")
    assert ("set_network_profile", ("Biblioteca", "", "publico")) in env.daemon.calls
    env.win.networks.ssid_entry.set_text("   ")
    env.win.networks.add_btn.emit("clicked")
    assert len([c for c in env.daemon.calls if c[0] == "set_network_profile"]) == 1


def test_log_page_shows_events_and_blocked_attempts(env):
    env.win.log.load()
    t = texts(env.win.log)
    assert "Service started" in t and "Confirmed Home" in t
    assert "203.0.113.9 tried to reach 192.168.1.5:22" in t


def test_settings_send_options_to_the_daemon(env):
    env.win.settings.load()
    env.win.settings.router.set_active(True)
    env.win.settings.log_level.set_selected(2)
    assert ("set_options", (True, "medium")) in env.daemon.calls
    assert env.daemon.options == {"router_mode": True, "log_level": "medium"}


def test_settings_do_not_write_back_while_loading(env):
    env.daemon.options = {"router_mode": True, "log_level": "high"}
    before = len([c for c in env.daemon.calls if c[0] == "set_options"])
    env.win.settings.load()
    assert env.win.settings.router.get_active()
    assert len([c for c in env.daemon.calls if c[0] == "set_options"]) == before


def test_language_and_notification_switches_call_back(env):
    assert env.win.settings.language.get_selected() == 1  # English is the current language
    env.win.settings.language.set_selected(0)  # Português
    env.win.settings.notifications.set_active(False)
    assert env.changes["lang"] == ["pt"] and env.changes["notifications"] == [False]


# -- slow applies ---


def test_applying_shows_progress_until_the_service_answers(gtk):
    """ufw can take seconds: the row says so, and nothing else can be started meanwhile."""
    from gi.repository import Adw

    from fwmanager.ui import settings as us
    from fwmanager.ui.window import MainWindow

    jobs = []

    def later(fn, done):
        jobs.append((fn, done))

    def finish_all():
        while jobs:
            fn, done = jobs.pop(0)
            try:
                result, error = fn(), None
            except FwError as exc:
                result, error = None, exc
            done(result, error)
        pump(0.1)

    daemon = FakeDaemon()
    controller = Controller(lambda: FakeClient(daemon), run=later, user="ana")
    app = Adw.Application(application_id="org.fwmanager.TestSlow")
    app.register()
    win = MainWindow(app, controller, us.UserSettings(setup_stage="done"), print, print)
    win.present()
    try:
        finish_all()
        finish_all()  # the pages load after the first status
        rows = {r.name_: r for r in win.home._profile_rows}
        rows["casa"].apply_btn.emit("clicked")
        pump(0.1)
        assert rows["casa"].applying.get_visible() and not rows["casa"].apply_btn.get_visible()
        assert "Applying…" in texts(rows["casa"])
        assert not rows["trabalho"].apply_btn.get_sensitive()  # one change at a time
        assert not rows["trabalho"].test_btn.get_sensitive()
        finish_all()
        finish_all()
        assert not rows["casa"].applying.get_visible()
        assert win.home.pending_group.get_visible()  # now it waits for confirmation
    finally:
        controller.stop_polling()
        win.destroy()


def test_an_open_wifi_is_flagged_on_the_overview_and_networks_pages(env):
    from fwmanager.ui.netinfo import CurrentNetwork

    net = CurrentNetwork("Café", "Café", "u-1", "802-11-wireless", open=True)
    env.win.home.set_current_network(net)
    assert "Wi-Fi: Café (no password)" in texts(env.win.home)
    page = env.win.networks
    page._current_loaded(net, None)
    assert page.current_row.get_subtitle() == "No password: anyone nearby can join"
    page.current_row.set_selected(0)  # Home
    assert page.open_warning.get_visible() and "no password" in page.open_warning.get_title()
    page.current_row.set_selected(2)  # Public: nothing to warn about
    assert not page.open_warning.get_visible()
    page._current_loaded(CurrentNetwork("Casa", "Casa", "u-2", "802-11-wireless"), None)
    page.current_row.set_selected(0)
    assert not page.open_warning.get_visible()  # it has a password


def test_forgetting_a_network_can_be_undone(env):
    env.win.networks.load()
    ssid, uuid, profile = env.daemon.networks[0]
    env.win.networks.forget(ssid, uuid, profile)
    assert env.daemon.networks == []
    toast = env.win.networks.undo_toast
    assert toast.get_title() == "Forgot Casa WiFi." and toast.get_button_label() == "Undo"
    toast.emit("button-clicked")
    assert env.daemon.networks == [(ssid, uuid, profile)]
    assert "Casa WiFi" in texts(env.win.networks)


# -- closing with a change still pending ---


@pytest.mark.parametrize(
    "response,call,closed",
    [("keep", "confirm", True), ("undo", "rollback", True), ("cancel", None, False)],
)
def test_closing_with_a_pending_change_asks_first(env, response, call, closed):
    env.daemon.pending = "casa"
    env.controller.refresh()
    env.win.close()
    pump(0.2)
    assert env.win.get_visible(), "closed without asking"
    dialog = env.win.close_dialog
    assert dialog.get_heading() == "Keep the new profile?" and "Home" in dialog.get_body()
    env.daemon.calls.clear()
    dialog.emit("response", response)
    dialog.force_close()  # what libadwaita does right after a button's response
    pump(0.2)
    if call:
        assert (call, ()) in env.daemon.calls
    assert env.win.get_visible() is not closed


def test_closing_with_nothing_pending_just_closes(env):
    env.win.close()
    pump(0.2)
    assert not env.win.get_visible()


def test_the_profile_editor_checks_networks_while_typing(env):
    d = editor(env, "personalizado")
    d.source.set_selected(2)  # Specific networks
    d.cidrs.set_text("192.168.1.0/16")
    assert d.cidr_problem.get_visible() and "192.168.0.0/16" in d.cidr_problem.get_title()
    assert d.cidrs.has_css_class("error") and not d.save_btn.get_sensitive()
    d.cidrs.set_text("192.168.0.0/16")
    assert not d.cidr_problem.get_visible() and d.save_btn.get_sensitive()
    d.source.set_selected(1)  # Local network: nothing to type
    d.cidrs.set_text("rubbish")
    assert not d.cidr_problem.get_visible() and d.save_btn.get_sensitive()


def test_errors_from_the_service_show_inside_the_editor(env):
    d = editor(env)
    env.daemon.error = FwError("bad", code="store_error")
    d.save_btn.emit("clicked")
    assert d.error_row.get_visible() and d.error_row.get_title() == "Could not save the settings."


def test_a_refused_setting_snaps_back_to_what_is_in_effect(env, monkeypatch):
    env.win.settings.load()
    assert not env.win.settings.router.get_active()

    def refuse(self, *args):
        raise FwError("disk full", code="store_error")

    monkeypatch.setattr(FakeClient, "set_options", refuse)
    env.win.settings.router.set_active(True)
    pump(0.2)
    assert not env.win.settings.router.get_active()  # not left showing an unsaved choice


def test_settings_end_with_the_credits(env):
    assert "Made by Tomascody-web333 with help of Claude Code" in texts(env.win.settings)
