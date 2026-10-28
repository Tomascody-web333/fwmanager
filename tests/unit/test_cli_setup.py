"""`fwmanager-cli setup` / `uninstall` against a simulated machine (service, group, helper)."""

import os
import pwd

import pytest
from helpers.fake_client import FakeClient, FakeDaemon

from fwmanager.cli import setup_cmd as sc
from fwmanager.errors import FwError
from fwmanager.i18n import set_language
from fwmanager.ui import privileged
from fwmanager.ui.setup_state import GroupInfo

ME = pwd.getpwuid(os.getuid()).pw_name


@pytest.fixture(autouse=True)
def english():
    set_language(None)
    yield
    set_language(None)


class World:
    """The machine: is the service up, is the account in the group, what the helper does."""

    def __init__(self, service=True, listed=True, held=True, ufw=True, configured=False):
        self.daemon = FakeDaemon()
        self.daemon.configured = configured
        self.service, self.listed, self.held, self.ufw = service, listed, held, ufw
        self.helper_calls, self.relaunches, self.answers = [], [], []
        self.helper_result = privileged.HelperResult(0)
        self.network = None

    def client(self):
        if not self.service:
            raise FwError("down", code="daemon_unavailable")
        if not self.held:
            raise FwError("no", code="access_denied")
        return FakeClient(self.daemon)

    def run_helper(self, verb):
        self.helper_calls.append(verb)
        if self.helper_result.ok and verb == "setup":
            self.service, self.listed = True, True  # held only in a new process
        return self.helper_result

    def deps(self, euid=1000, env=None, interactive=True):
        return sc.Deps(
            client=self.client, run_helper=self.run_helper,
            group_info=lambda user: GroupInfo(True, self.listed, self.held),
            ufw_present=lambda: self.ufw,
            relaunch=lambda args: self.relaunches.append(args) or True,
            ask=lambda prompt: self.answers.pop(0), interactive=lambda: interactive,
            env=env or {}, euid=lambda: euid,
            current_network=lambda: self.network,
            as_root_setup=lambda env: self.helper_calls.append(("root-setup", env)) or 0,
            as_root_uninstall=lambda: self.helper_calls.append("root-uninstall") or 0,
        )  # fmt: skip


def test_already_set_up_elsewhere_is_not_done_twice(capsys):
    w = World(configured=True)
    assert sc.setup(w.deps()) == 0
    assert w.helper_calls == [] and "already set up" in capsys.readouterr().out


def test_a_fresh_machine_asks_once_then_carries_on_with_the_new_group(capsys):
    w = World(service=False, listed=False, held=False)
    assert sc.setup(w.deps(), profile="home", yes=True) == 0
    assert w.helper_calls == ["setup"]  # one password, for everything
    assert w.relaunches == [["setup", "--profile", "home", "--yes"]]  # continues in a new process
    w.held = True  # ...which has the group
    assert sc.setup(w.deps(), profile="home", yes=True) == 0
    assert w.helper_calls == ["setup"]  # not asked again
    assert w.daemon.active == "home" and w.daemon.configured
    assert "You are all set" in capsys.readouterr().out
    assert sc.setup(w.deps()) == 0 and w.helper_calls == ["setup"]  # and now it is just done


def test_the_profile_is_asked_in_the_terminal(capsys):
    w = World()
    w.answers = ["home"]
    assert sc.setup(w.deps(), yes=True) == 0
    out = capsys.readouterr().out
    assert "How should this computer behave on this network?" in out and "Lets nothing in." in out
    assert w.daemon.active == "home"


def test_without_a_terminal_it_needs_the_profile_given(capsys):
    w = World()
    assert sc.setup(w.deps(interactive=False)) == 1
    assert "--profile NAME --yes" in capsys.readouterr().err


def test_ufw_missing_or_another_firewall_needs_a_command_first(capsys):
    w = World(ufw=False, service=False)
    assert sc.setup(w.deps()) == 1
    assert "sudo apt install ufw" in capsys.readouterr().out and w.helper_calls == []


@pytest.mark.parametrize(
    "code,exit_code,message",
    [(privileged.DISMISSED, 130, "Cancelled"), (privileged.NOT_AUTHORISED, 1, "Not authorised"),
     (-1, 1, "not available")],
)  # fmt: skip
def test_a_helper_that_did_not_finish_changes_nothing(capsys, code, exit_code, message):
    w = World(service=False, listed=False, held=False)
    w.helper_result = privileged.HelperResult(code)
    assert sc.setup(w.deps()) == exit_code
    assert message in capsys.readouterr().err and w.relaunches == []


def test_under_sudo_the_person_who_typed_it_is_set_up():
    w = World(listed=False)
    uid = str(os.getuid() if os.getuid() else 65534)
    sc.setup(w.deps(euid=0, env={"SUDO_UID": uid}), profile="home", yes=True)
    assert w.helper_calls[0] == ("root-setup", {"PKEXEC_UID": uid})


def test_plain_root_is_told_to_use_a_normal_account(capsys):
    assert sc.setup(World().deps(euid=0, env={})) == 1
    assert "normal user" in capsys.readouterr().err


def test_uninstall_says_what_happens_and_needs_a_yes(capsys):
    w = World()
    w.answers = ["no"]
    assert sc.uninstall(w.deps()) == 130 and w.helper_calls == []
    out = capsys.readouterr().out
    assert "Rules you added to ufw yourself are kept." in out
    w.answers = ["yes"]
    assert sc.uninstall(w.deps()) == 0 and w.helper_calls == ["uninstall"]
    assert "fwmanager was removed" in capsys.readouterr().out


def test_uninstall_yes_and_sudo(capsys):
    w = World()
    assert sc.uninstall(w.deps(), yes=True) == 0 and w.helper_calls == ["uninstall"]
    assert sc.uninstall(w.deps(euid=0), yes=True) == 0
    assert w.helper_calls[-1] == "root-uninstall"


def test_uninstall_without_a_terminal_needs_yes(capsys):
    assert sc.uninstall(World().deps(interactive=False)) == 1


def test_a_failed_uninstall_shows_the_manual_command(capsys):
    w = World()
    w.helper_result = privileged.HelperResult(1)
    assert sc.uninstall(w.deps(), yes=True) == 1
    assert "/usr/local/libexec/fwmanager/uninstall" in capsys.readouterr().err


def test_the_chosen_profile_is_remembered_for_this_network(capsys):
    from fwmanager.ui.netinfo import WIFI, CurrentNetwork

    w = World()
    w.daemon.networks = []
    w.network = CurrentNetwork("Home Wi-Fi", "Home Wi-Fi", "u-1", WIFI)
    assert sc.setup(w.deps(), profile="home", yes=True) == 0
    assert w.daemon.networks == [("Home Wi-Fi", "u-1", "home")]


def test_an_open_wifi_gets_the_same_warning_as_in_the_window(capsys):
    from fwmanager.ui.netinfo import WIFI, CurrentNetwork

    w = World()
    w.network = CurrentNetwork("Cafe", "Cafe", "u-2", WIFI, open=True)
    sc.setup(w.deps(), profile="home", yes=True)
    assert "no password" in capsys.readouterr().out
