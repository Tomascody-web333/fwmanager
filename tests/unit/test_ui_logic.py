import pytest
from gi.repository import GLib

from fwmanager.errors import FwError
from fwmanager.i18n import set_language
from fwmanager.ui import settings as us
from fwmanager.ui import state
from fwmanager.ui.controller import Controller
from fwmanager.ui.worker import run_async, run_sync


@pytest.fixture(autouse=True)
def english():
    set_language(None)
    yield
    set_language(None)


# -- user settings ---


def test_settings_defaults_when_missing(tmp_path):
    assert us.load(tmp_path / "nope.toml") == us.UserSettings(None, True)


def test_settings_roundtrip(tmp_path):
    p = tmp_path / "fwmanager" / "settings.toml"
    us.save(us.UserSettings("pt", False), p)
    assert us.load(p) == us.UserSettings("pt", False)
    us.save(us.UserSettings(None, True), p)
    assert us.load(p) == us.UserSettings(None, True) and "lang" not in p.read_text()


@pytest.mark.parametrize(
    "content,expected",
    [
        ('lang = "klingon"\n', us.UserSettings(None, True)),
        ('lang = 5\nnotifications = "yes"\n', us.UserSettings(None, True)),
        ("this is = = broken", us.UserSettings(None, True)),
        ('lang = "en"\nnotifications = false\n', us.UserSettings("en", False)),
    ],
)
def test_settings_ignore_bad_values(tmp_path, content, expected):
    p = tmp_path / "s.toml"
    p.write_text(content)
    assert us.load(p) == expected


def test_settings_path_honours_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert us.settings_path() == tmp_path / "fwmanager" / "settings.toml"


def test_with_lang_rejects_unknown():
    assert us.UserSettings().with_lang("pt").lang == "pt"
    with pytest.raises(ValueError):
        us.UserSettings().with_lang("fr")


# -- which screen ---

OKAY = {"ufw_installed": True, "blocking_conflicts": [], "firewall_active": True}


def err(code):
    return FwError("x", code=code)


@pytest.mark.parametrize(
    "status,error,expected",
    [
        (OKAY, None, state.OK),
        (None, err("access_denied"), state.NOT_IN_GROUP),
        (None, err("daemon_unavailable"), state.DAEMON_DOWN),
        (None, err("internal_error"), state.DAEMON_DOWN),
        (None, None, state.DAEMON_DOWN),
        ({**OKAY, "ufw_installed": False}, None, state.UFW_MISSING),
        ({**OKAY, "blocking_conflicts": ["firewalld"]}, None, state.CONFLICT),
    ],
)
def test_classify(status, error, expected):
    assert state.classify(status, error) == expected


def test_problem_screens_carry_the_command_to_fix_them():
    p = state.problem_for(state.NOT_IN_GROUP, None, "ana")
    assert p.command == "sudo usermod -aG fwmanager ana" and "log out" in p.body
    assert state.problem_for(state.DAEMON_DOWN, None, "ana").command.endswith("fwmanagerd")
    assert state.problem_for(state.UFW_MISSING, None, "ana").command == "sudo apt install ufw"
    c = state.problem_for(state.CONFLICT, {"blocking_conflicts": ["firewalld"]}, "ana")
    assert "firewalld" in c.body and c.command == "sudo systemctl disable --now firewalld.service"
    assert state.problem_for(state.OK, OKAY, "ana") is None


def test_problem_text_is_translated(tmp_path):
    from fwmanager import i18n_build as ib

    (tmp_path / "po").mkdir()
    (tmp_path / "po/pt_PT.po").write_text(
        'msgid "ufw is not installed"\nmsgstr "O ufw não está instalado"\n'
    )
    ib.compile_dir(tmp_path / "po", tmp_path / "loc")
    set_language("pt", str(tmp_path / "loc"))
    assert state.problem_for(state.UFW_MISSING, None, "x").title == "O ufw não está instalado"
    assert state.problem_for(state.UFW_MISSING, None, "x").command == "sudo apt install ufw"


# -- text helpers ---


def test_profile_titles_and_unknown_names():
    assert [state.profile_title(n) for n in state.PROFILE_ORDER] == [
        "Home", "Work", "Public", "Custom"]  # fmt: skip
    assert state.profile_title("mine") == "mine" and state.profile_hint("mine") == ""


def test_describe_profile():
    details = {"services": ["ssh", "samba"], "source_kind": "lan", "ping": True, "mdns": False}
    text = state.describe_profile(details, {"ssh": "SSH", "samba": "File sharing (Samba)"})
    assert text == "SSH, File sharing (Samba) · from: Local network only · ping: yes · mDNS: no"
    closed = state.describe_profile({**details, "services": []}, {})
    assert closed.startswith("nothing is open")


def test_parse_cidrs():
    assert state.parse_cidrs("10.0.0.0/8, 192.168.0.0/16\n172.16.0.0/12") == [
        "10.0.0.0/8", "192.168.0.0/16", "172.16.0.0/12"]  # fmt: skip
    assert state.parse_cidrs("  ,, ") == []


def test_event_text():
    assert state.event_text("applied", "casa") == "Applied Home, waiting for confirmation"
    assert state.event_text("reverted", "publico") == "Undid Public"
    assert state.event_text("denied", "").startswith("Refused")
    assert state.event_text("brand_new", "x") == "brand_new"  # unknown codes stay visible


def test_countdown_text():
    assert state.countdown_text("casa", 12) == "Home will be undone in 12 s unless you confirm."


# -- worker ---


def pump_until(cond, seconds=3):
    ctx = GLib.MainContext.default()
    end = GLib.get_monotonic_time() + seconds * 1_000_000
    while not cond() and GLib.get_monotonic_time() < end:
        ctx.iteration(False)
        GLib.usleep(2000)
    return cond()


def test_run_async_delivers_on_the_main_loop():
    got = []
    run_async(lambda: 41 + 1, lambda r, e: got.append((r, e)))
    assert pump_until(lambda: got) and got == [(42, None)]


def test_run_async_reports_errors_and_never_dies_silently():
    got = []
    run_async(
        lambda: (_ for _ in ()).throw(FwError("no", code="no_pending")), lambda r, e: got.append(e)
    )
    run_async(lambda: 1 / 0, lambda r, e: got.append(e))
    assert pump_until(lambda: len(got) == 2)
    assert sorted(e.code for e in got) == ["internal_error", "no_pending"]


# -- controller ---


class FakeClient:
    def __init__(self, status=None, error=None):
        self._status = status if status is not None else dict(OKAY, pending_profile="")
        self.error = error
        self.calls = []
        self.signal_cb = None

    def subscribe(self, cb):
        self.signal_cb = cb

    def status(self):
        if self.error:
            raise self.error
        return self._status

    def list_profiles(self):
        return ["casa", "zeta", "publico", "trabalho"]

    def get_profile(self, name):
        return {"name": name, "services": ["ssh"], "ping": True, "mdns": False,
                "source_kind": "lan", "cidrs": [], "customized": False}  # fmt: skip

    def list_services(self):
        return [("ssh", "SSH")]

    def __getattr__(self, name):
        def call(*args):
            self.calls.append((name, args))
            if self.error:
                raise self.error

        return call


def controller(client):
    return Controller(lambda: client, run=run_sync, user="ana")


def test_refresh_publishes_a_snapshot():
    c = controller(FakeClient())
    seen = []
    c.listeners.append(seen.append)
    c.refresh()
    assert seen[-1].screen == state.OK and seen[-1].problem is None


def test_refresh_when_the_daemon_is_down_shows_the_fix():
    c = controller(FakeClient(error=err("daemon_unavailable")))
    c.refresh()
    assert c.snapshot.screen == state.DAEMON_DOWN and "systemctl" in c.snapshot.problem.command


def test_not_in_group_names_the_user():
    c = controller(FakeClient(error=err("access_denied")))
    c.refresh()
    assert c.snapshot.screen == state.NOT_IN_GROUP
    assert c.snapshot.problem.command == "sudo usermod -aG fwmanager ana"


def test_client_that_cannot_connect_yields_the_daemon_down_screen():
    def broken():
        raise FwError("no bus", code="daemon_unavailable")

    c = Controller(broken, run=run_sync, user="ana")
    seen = []
    c.listeners.append(seen.append)
    c.refresh()
    assert seen[-1].screen == state.DAEMON_DOWN


def test_operations_call_the_daemon_and_refresh():
    client = FakeClient()
    c = controller(client)
    results = []
    c.apply("casa", lambda r, e: results.append(e))
    c.confirm(lambda r, e: results.append(e))
    c.rollback(lambda r, e: results.append(e))
    assert [n for n, _ in client.calls] == ["apply_profile", "confirm", "rollback"]
    assert results == [None, None, None] and c.snapshot.screen == state.OK


def test_failed_operation_reports_the_error_and_rechecks_the_screen():
    client = FakeClient()
    c = controller(client)
    c.refresh()
    client.error = err("access_denied")
    got = []
    c.apply("casa", lambda r, e: got.append(e))
    assert got[0].code == "access_denied" and c.snapshot.screen == state.NOT_IN_GROUP


def test_load_profiles_orders_for_display():
    c = controller(FakeClient())
    out = []
    c.load_profiles(lambda r, e: out.append(r))
    profiles, titles = out[0]
    assert list(profiles) == ["casa", "trabalho", "publico", "zeta"] and titles == {"ssh": "SSH"}


def test_signals_trigger_a_refresh_and_reach_listeners():
    client = FakeClient()
    c = controller(client)
    heard, seen = [], []
    c.signal_listeners.append(lambda n, a: heard.append(n))
    c.listeners.append(seen.append)
    client.signal_cb("Reverted", ("casa",))
    assert heard == ["Reverted"] and seen


def test_settings_style_operations_forward_arguments():
    client = FakeClient()
    c = controller(client)
    c.set_options(True, "low")
    c.save_profile("casa", ["ssh"], True, False, "lan", [])
    c.set_network("Cafe", "", "publico")
    c.remove_network("Cafe", "")
    c.reset_profile("casa")
    names = [n for n, _ in client.calls]
    assert names == ["set_options", "set_profile", "set_network_profile", "remove_network",
                     "reset_profile"]  # fmt: skip
    assert client.calls[0][1] == (True, "low")
