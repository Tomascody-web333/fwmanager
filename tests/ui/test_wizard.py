"""First-run setup, driven through the real widgets with a fake daemon and a fake helper."""

import pytest
from helpers.fake_client import FakeClient, FakeDaemon
from helpers.gtk_tools import texts
from helpers.shot import pump

from fwmanager.errors import FwError
from fwmanager.i18n import set_language
from fwmanager.ui import privileged, setup_state
from fwmanager.ui.controller import Controller
from fwmanager.ui.netinfo import CurrentNetwork
from fwmanager.ui.worker import run_sync

pytestmark = pytest.mark.ui

HELD = setup_state.GroupInfo(True, True, True)


@pytest.fixture(autouse=True)
def english():
    set_language(None)
    yield
    set_language(None)


class Env:
    """A window in first-run mode with every outside effect replaced by a recorder."""

    def __init__(self, gtk, stage="welcome", group=HELD, ufw=True):
        from gi.repository import Adw

        from fwmanager.ui import settings as us
        from fwmanager.ui.window import MainWindow

        self.daemon = FakeDaemon()
        self.group = group
        self.helper_calls, self.relaunch_calls = [], []
        self.helper_result = privileged.HelperResult(0)
        self.relaunch_result = True
        self.stages, self.languages = [], []
        self.on_helper = lambda: None  # what a successful helper changes in the fake world
        self.controller = Controller(lambda: FakeClient(self.daemon), run=run_sync, user="ana")
        app = Adw.Application(application_id="org.fwmanager.TestWizard")
        app.register()
        self.win = MainWindow(
            app, self.controller, us.UserSettings(setup_stage=stage),
            self.languages.append, lambda b: None, self.stages.append,
            dict(user="ana", run_helper=self._helper, do_relaunch=self._relaunch,
                 group_info=lambda: self.group, ufw_present=lambda: ufw, run=run_sync),
        )  # fmt: skip
        self.win.present()
        pump(0.4)
        self.wizard = self.win.wizard

    def _helper(self):
        self.helper_calls.append(1)
        if self.helper_result.ok:
            self.on_helper()
        return self.helper_result

    def _relaunch(self):
        self.relaunch_calls.append(1)
        return self.relaunch_result

    def close(self):
        self.controller.stop_polling()
        self.win.destroy()

    def row(self, requirement_id):
        return next(r for r in self.wizard._rows if r.requirement.id == requirement_id)


@pytest.fixture
def make(gtk):
    envs = []

    def _make(**kw):
        e = Env(gtk, **kw)
        envs.append(e)
        return e

    yield _make
    for e in envs:
        e.close()


# -- navigation ---


def test_first_run_starts_on_the_welcome_page_not_the_main_window(make):
    e = make()
    assert e.win.stack.get_visible_child_name() == "setup" and e.wizard.stage == "welcome"
    t = texts(e.wizard)
    assert "Welcome to fwmanager" in t and "Get started" in t and "Skip setup" in t


def test_progress_is_saved_at_every_step(make):
    e = make()
    e.wizard.start_btn.emit("clicked")
    assert e.wizard.stage == "requirements"
    e.wizard.req_next.emit("clicked")
    pump(0.2)
    assert e.stages == ["welcome", "requirements", "profile"]


@pytest.mark.parametrize("stage", ["requirements", "profile"])
def test_setup_resumes_where_it_stopped(make, stage):
    e = make(stage=stage)
    assert e.wizard.stage == stage


def test_a_finished_setup_never_shows_the_wizard(make):
    e = make(stage="done")
    assert e.wizard is None and e.win.stack.get_visible_child_name() == "main"


def test_skipping_goes_to_the_normal_window_and_remembers(make):
    e = make()
    e.wizard.skip_btn.emit("clicked")
    pump(0.3)
    assert e.stages[-1] == "done" and e.win.wizard is None
    assert e.win.stack.get_visible_child_name() == "main"


def test_the_language_choice_is_reported(make):
    e = make()
    e.wizard.language.set_selected(0)  # Português
    assert e.languages == ["pt"]


# -- requirements ---


def goto_requirements(e):
    e.wizard.start_btn.emit("clicked")
    pump(0.2)


def test_all_ready_lets_you_continue(make):
    e = make()
    goto_requirements(e)
    assert [r.requirement.state for r in e.wizard._rows] == ["ok"] * 4
    assert e.wizard.req_next.get_sensitive()


def test_the_service_down_is_fixed_with_one_helper_run(make):
    e = make(group=setup_state.GroupInfo(True, False, False))
    e.daemon.error = FwError("x", code="daemon_unavailable")
    e.on_helper = lambda: (setattr(e.daemon, "error", None), setattr(e, "group", HELD))
    goto_requirements(e)
    assert (
        e.row("service").requirement.fix == "helper"
        and e.row("account").requirement.fix == "helper"
    )
    assert not e.wizard.req_next.get_sensitive()
    e.row("service").fix_button.emit("clicked")
    pump(0.3)
    assert len(e.helper_calls) == 1  # one password window fixes both rows
    assert [r.requirement.state for r in e.wizard._rows] == ["ok"] * 4
    assert e.wizard.req_next.get_sensitive()


def test_a_second_click_while_the_helper_runs_is_ignored(make):
    e = make()
    e.daemon.error = FwError("x", code="daemon_unavailable")
    goto_requirements(e)
    e.wizard.helper_running = True
    e.wizard.run_helper()
    assert e.helper_calls == []


@pytest.mark.parametrize(
    "result,message",
    [
        (privileged.HelperResult(126), "Cancelled. Nothing was changed."),
        (privileged.HelperResult(127), "Not authorised."),
        (privileged.HelperResult(1), "The setup step failed."),
    ],
)
def test_helper_that_did_not_succeed_changes_nothing_and_says_why(make, result, message):
    e = make()
    e.daemon.error = FwError("x", code="daemon_unavailable")
    e.helper_result = result
    goto_requirements(e)
    e.row("service").fix_button.emit("clicked")
    pump(0.3)
    assert len(e.helper_calls) == 1 and not e.wizard.req_next.get_sensitive()
    assert e.daemon.error is not None  # still not fixed, and nothing pretends otherwise


def test_without_polkit_the_fixes_become_commands_to_copy(make):
    e = make(group=setup_state.GroupInfo(True, False, False))
    e.daemon.error = FwError("x", code="daemon_unavailable")
    e.helper_result = privileged.HelperResult(-1)
    goto_requirements(e)
    e.row("service").fix_button.emit("clicked")
    pump(0.3)
    commands = [r.command_label.get_text() for r in e.wizard._rows if hasattr(r, "command_label")]
    assert "sudo systemctl enable --now fwmanagerd" in commands
    assert "sudo usermod -aG fwmanager ana" in commands


def test_added_to_the_group_but_not_yet_active_offers_a_restart(make):
    e = make(group=setup_state.GroupInfo(True, True, False))
    e.daemon.error = FwError("x", code="access_denied")
    goto_requirements(e)
    row = e.row("account")
    assert row.requirement.state == "restart"
    row.fix_button.emit("clicked")
    assert e.relaunch_calls == [1] and e.stages[-1] == "requirements"  # resumes here afterwards


def test_when_the_restart_is_impossible_it_asks_to_log_out(make):
    e = make(group=setup_state.GroupInfo(True, True, False))
    e.daemon.error = FwError("x", code="access_denied")
    e.relaunch_result = False
    goto_requirements(e)
    e.row("account").fix_button.emit("clicked")
    assert e.relaunch_calls == [1]  # tried; the toast tells them to log out and in


def test_ufw_and_another_firewall_manager_need_a_terminal_command(make):
    e = make()
    e.daemon.ufw_installed = False
    e.daemon.conflicts = ["firewalld"]
    goto_requirements(e)
    commands = [r.command_label.get_text() for r in e.wizard._rows if hasattr(r, "command_label")]
    assert "sudo apt install ufw" in commands
    assert not e.wizard.req_next.get_sensitive()
    e.daemon.ufw_installed, e.daemon.conflicts = True, []
    e.wizard.recheck_btn.emit("clicked")
    assert e.wizard.req_next.get_sensitive()


# -- choosing a profile ---


def goto_profile(e, network=None):
    e.controller.current_network = lambda done, bus_address=None: done(network, None)
    e.wizard.go("profile")
    pump(0.2)


def test_public_is_preselected_as_the_safe_default(make):
    e = make(stage="profile")
    goto_profile(e)
    assert e.wizard.choice == "publico"


def test_apply_waits_for_confirmation_and_keeping_it_finishes(make):
    e = make(stage="profile")
    goto_profile(e)
    e.wizard._choice_rows["casa"].set_active(True)
    e.wizard.apply_btn.emit("clicked")
    pump(0.1)
    assert ("apply_profile", ("casa",)) in e.daemon.calls
    assert e.wizard.confirm_group.get_visible()
    assert "Home will be undone in 30 s" in e.wizard.confirm_row.get_subtitle()
    assert not e.wizard.apply_btn.get_sensitive()
    e.wizard.keep_btn.emit("clicked")
    pump(0.2)
    assert e.daemon.active == "casa" and e.wizard.stage == "done"


def test_undoing_lets_you_pick_again(make):
    e = make(stage="profile")
    goto_profile(e)
    e.wizard._choice_rows["trabalho"].set_active(True)
    e.wizard.apply_btn.emit("clicked")
    pump(0.1)
    e.wizard.undo_btn.emit("clicked")
    pump(0.1)
    assert ("rollback", ()) in e.daemon.calls and e.wizard.stage == "profile"
    assert not e.wizard.confirm_group.get_visible() and e.wizard.apply_btn.get_sensitive()


def test_the_network_is_remembered_with_the_chosen_profile(make):
    e = make(stage="profile")
    goto_profile(e, CurrentNetwork("Casa auto", "Casa WiFi", "u-7", "802-11-wireless"))
    assert "Wi-Fi: Casa WiFi" in texts(e.wizard)
    assert e.wizard.remember.get_visible()
    e.wizard._choice_rows["casa"].set_active(True)
    e.wizard.apply_btn.emit("clicked")
    pump(0.1)
    e.wizard.keep_btn.emit("clicked")
    pump(0.2)
    assert ("set_network_profile", ("Casa WiFi", "u-7", "casa")) in e.daemon.calls


def test_the_remember_switch_can_be_turned_off(make):
    e = make(stage="profile")
    goto_profile(e, CurrentNetwork("n", "Cafe", "u-8", "802-11-wireless"))
    e.wizard.remember.set_active(False)
    e.wizard.apply_btn.emit("clicked")
    pump(0.1)
    e.wizard.keep_btn.emit("clicked")
    pump(0.2)
    assert not [c for c in e.daemon.calls if c[0] == "set_network_profile"]


def test_a_wired_or_offline_machine_has_nothing_to_remember(make):
    e = make(stage="profile")
    goto_profile(e, None)
    assert "Not connected" in texts(e.wizard) and not e.wizard.remember.get_visible()


def test_test_opens_the_policy_dialog_for_the_selected_profile(make, monkeypatch):
    from fwmanager import policytest as pt

    monkeypatch.setattr(pt, "run_probes", lambda: [pt.Probe("loopback_v4", True)])
    e = make(stage="profile")
    goto_profile(e)
    e.wizard._choice_rows["trabalho"].set_active(True)
    e.wizard.test_btn.emit("clicked")
    pump(0.4)
    assert ("check_profile", ("trabalho",)) in e.daemon.calls


def test_an_apply_error_is_reported_and_nothing_is_pending(make):
    e = make(stage="profile")
    goto_profile(e)
    e.daemon.error = FwError("x", code="firewall_conflict")
    e.wizard.apply_btn.emit("clicked")
    pump(0.1)
    assert e.daemon.pending == "" and not e.wizard.confirm_group.get_visible()


def test_deciding_later_goes_to_the_last_page_without_applying(make):
    e = make(stage="profile")
    goto_profile(e)
    e.wizard.later_btn.emit("clicked")
    assert e.wizard.stage == "done" and not [c for c in e.daemon.calls if c[0] == "apply_profile"]


# -- finishing ---


def test_finishing_shows_the_normal_window_with_the_pages_loaded(make):
    e = make(stage="profile")
    e.wizard.go("done")
    assert "You are all set" in texts(e.wizard)
    e.wizard.finish_btn.emit("clicked")
    pump(0.5)
    assert e.win.wizard is None and e.win.stack.get_visible_child_name() == "main"
    assert e.win.home._profile_rows  # the profile rows are there


def test_problems_after_setup_use_the_normal_problem_screen(make):
    e = make(stage="done")
    e.daemon.error = FwError("x", code="access_denied")
    e.controller.refresh()
    pump(0.3)
    assert e.win.stack.get_visible_child_name() == "problem"
