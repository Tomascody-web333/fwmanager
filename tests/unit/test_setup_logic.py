import grp
import pwd
import subprocess

import pytest

from fwmanager import setup_helper as sh
from fwmanager.ui import privileged, relaunch, setup_state, state
from fwmanager.ui import settings as us

# -- the privileged helper ---


def lookup(uid):
    users = {1000: "ana", 1001: "Bad Name", 1002: "x;rm"}
    if uid not in users:
        raise KeyError(uid)
    return pwd.struct_passwd((users[uid], "x", uid, uid, "", "/home/x", "/bin/sh"))


class Recorder:
    def __init__(self, fail_on=None):
        self.calls, self.fail_on = [], fail_on

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        code = 1 if self.fail_on and self.fail_on in argv[0] else 0
        return subprocess.CompletedProcess(argv, code, "", "")


def run_setup(env, rec=None, has_group=lambda: True):
    rec = rec or Recorder()
    return sh.setup(env, rec, lookup, has_group), rec


def test_adds_the_calling_account_and_starts_the_service():
    code, rec = run_setup({"PKEXEC_UID": "1000"})
    assert code == 0
    assert [c[1:] for c in rec.calls] == [
        ["-aG", "fwmanager", "ana"],
        ["enable", "--now", "fwmanagerd.service"],
    ]
    assert all(c[0].startswith("/") for c in rec.calls)  # absolute paths only


def test_creates_the_group_when_it_is_missing():
    _code, rec = run_setup({"PKEXEC_UID": "1000"}, has_group=lambda: False)
    assert rec.calls[0][1:] == ["--system", "fwmanager"]


@pytest.mark.parametrize(
    "env",
    [{}, {"PKEXEC_UID": ""}, {"PKEXEC_UID": "abc"}, {"PKEXEC_UID": "-1"}, {"PKEXEC_UID": "0"},
     {"PKEXEC_UID": "4242"}, {"PKEXEC_UID": "1001"}, {"PKEXEC_UID": "1002"},
     {"PKEXEC_UID": "1000; reboot"}, {"PKEXEC_UID": "²"}, {"PKEXEC_UID": "١٠٠٠"},
     {"PKEXEC_UID": " 1000"}, {"PKEXEC_UID": "1000\n"}],
)  # fmt: skip
def test_refuses_anything_that_is_not_a_plain_pkexec_caller(env):
    rec = Recorder()
    with pytest.raises(sh.Refused):
        sh.setup(env, rec, lookup, lambda: True)
    assert rec.calls == []  # nothing ran


def test_the_account_comes_only_from_pkexec_not_from_other_variables():
    env = {"PKEXEC_UID": "1000", "USER": "root", "SUDO_USER": "root", "LOGNAME": "root"}
    _code, rec = run_setup(env)
    assert rec.calls[0][-1] == "ana"


def test_a_failing_step_stops_and_reports():
    code, rec = run_setup({"PKEXEC_UID": "1000"}, Recorder(fail_on="usermod"))
    assert code == sh.EXIT_FAILED and len(rec.calls) == 1  # the service is not started


def test_main_only_accepts_the_one_verb(capsys):
    for argv in ([], ["setup", "extra"], ["shell"], ["setup; id"], ["--help"]):
        assert sh.main(argv) == sh.EXIT_USAGE
    assert "usage" in capsys.readouterr().err


def test_main_refuses_without_pkexec(monkeypatch, capsys):
    monkeypatch.delenv("PKEXEC_UID", raising=False)
    assert sh.main(["setup"]) == sh.EXIT_REFUSED
    assert "pkexec" in capsys.readouterr().err


def test_group_exists(tmp_path):
    f = tmp_path / "group"
    f.write_text("root:x:0:\nfwmanager:x:900:ana\n")
    assert sh.group_exists(f)
    f.write_text("root:x:0:\nfwmanager2:x:900:\n")
    assert not sh.group_exists(f)
    assert not sh.group_exists(tmp_path / "missing")


# -- running it from the interface ---


@pytest.mark.parametrize(
    "code,ok,dismissed,denied",
    [(0, True, False, False), (126, False, True, False), (127, False, False, True),
     (1, False, False, False)],
)  # fmt: skip
def test_helper_result_codes(code, ok, dismissed, denied):
    r = privileged.run_setup_helper(run=lambda *a, **k: subprocess.CompletedProcess(a, code))
    assert (r.ok, r.dismissed, r.not_authorised) == (ok, dismissed, denied) and r.available


def test_helper_runs_exactly_pkexec_helper_setup():
    seen = []
    privileged.run_setup_helper("/x/pkexec", "/x/helper", lambda argv, **k: (
        seen.append(argv), subprocess.CompletedProcess(argv, 0))[1])  # fmt: skip
    assert seen == [["/x/pkexec", "/x/helper", "setup"]]


def test_missing_pkexec_is_reported_not_raised():
    def boom(*a, **k):
        raise FileNotFoundError

    assert not privileged.run_setup_helper(run=boom).available


# -- relaunch ---


def test_relaunch_command_uses_sg_with_the_group():
    cmd = relaunch.relaunch_command("/usr/local/bin/fwmanager", sg="/usr/bin/sg")
    assert cmd == ["/usr/bin/sg", "fwmanager", "-c", "/usr/local/bin/fwmanager"]


def test_relaunch_quotes_odd_paths():
    cmd = relaunch.relaunch_command("/opt/my app/fw; id", sg="/usr/bin/sg")
    assert cmd[3] == "'/opt/my app/fw; id'"  # one quoted word, never a second command


def test_relaunch_executes_it_or_says_it_cannot(monkeypatch):
    calls = []
    assert relaunch.relaunch("/x/fwmanager", execv=lambda p, a: calls.append((p, a)))
    assert calls[0][1][1:3] == ["fwmanager", "-c"]
    monkeypatch.setattr(relaunch.shutil, "which", lambda name: None)
    assert relaunch.relaunch("/x/fwmanager", execv=lambda p, a: calls.append(1)) is False
    assert len(calls) == 1


# -- the requirement rows ---

GOOD = {"ufw_installed": True, "blocking_conflicts": [], "firewall_active": True}
HELD = setup_state.GroupInfo(True, True, True)


def rows(screen=state.OK, status=GOOD, group=HELD, ufw=True):
    return {r.id: r for r in setup_state.evaluate(screen, status, group, ufw, "ana")}


def test_everything_in_place():
    r = rows()
    assert setup_state.ready(list(r.values())) and all(x.ok for x in r.values())


def test_service_down_offers_the_one_time_fix():
    r = rows(state.DAEMON_DOWN, None)
    assert r["service"].fix == setup_state.FIX_HELPER and not r["service"].ok
    assert r["conflict"].state == setup_state.UNKNOWN  # cannot tell until it answers


def test_not_in_the_group_at_all():
    r = rows(state.NOT_IN_GROUP, None, setup_state.GroupInfo(True, False, False))
    assert r["account"].state == setup_state.MISSING and r["account"].fix == setup_state.FIX_HELPER
    assert r["account"].command == "sudo usermod -aG fwmanager ana"


def test_in_the_group_file_but_the_running_app_does_not_have_it_yet():
    r = rows(state.NOT_IN_GROUP, None, setup_state.GroupInfo(True, True, False))
    assert r["account"].state == setup_state.RESTART and r["account"].fix == setup_state.FIX_RESTART


def test_group_missing_entirely_counts_as_not_allowed():
    r = rows(state.DAEMON_DOWN, None, setup_state.GroupInfo(False, False, False))
    assert r["account"].state == setup_state.MISSING


def test_ufw_and_conflict_rows_give_a_command():
    r = rows(status={**GOOD, "ufw_installed": False, "blocking_conflicts": ["firewalld"]})
    assert r["ufw"].command == "sudo apt install ufw" and r["ufw"].fix == setup_state.FIX_COMMAND
    assert r["conflict"].command == "sudo systemctl disable --now firewalld.service"
    assert not setup_state.ready(list(r.values()))


def test_titles_are_translated_text():
    assert rows()["ufw"].title == "ufw is installed"


def test_group_info_reads_the_real_database_shape():
    fake_group = grp.struct_group(("fwmanager", "x", 900, ["ana"]))
    fake_pw = lambda name: pwd.struct_passwd((name, "x", 1000, 1000, "", "/h", "/s"))  # noqa: E731
    kw = dict(getgrnam=lambda n: fake_group, getpwnam=fake_pw, getegid=lambda: 1000)
    assert setup_state.group_info("fwmanager", "ana", getgroups=lambda: [1000, 900], **kw) == (
        setup_state.GroupInfo(True, True, True)
    )
    assert setup_state.group_info("fwmanager", "ana", getgroups=lambda: [1000], **kw) == (
        setup_state.GroupInfo(True, True, False)  # listed, but this process predates it
    )
    assert not setup_state.group_info("fwmanager", "bob", getgroups=lambda: [1000], **kw).listed

    def none(_):
        raise KeyError

    assert setup_state.group_info("fwmanager", "ana", getgrnam=none) == (
        setup_state.GroupInfo(False, False, False)
    )


# -- remembered progress ---


def test_setup_stage_is_remembered_and_validated(tmp_path):
    p = tmp_path / "s.toml"
    assert us.load(p).setup_stage == "welcome"
    us.save(us.UserSettings("pt", True, "profile"), p)
    assert us.load(p) == us.UserSettings("pt", True, "profile")
    p.write_text('setup_stage = "hacked"\n')
    assert us.load(p).setup_stage == "welcome"
    with pytest.raises(ValueError):
        us.UserSettings().with_stage("hacked")
    assert us.UserSettings().with_stage("done").setup_stage == "done"
