"""fwmanager-cli without a daemon: help, names, and what Ctrl+C leaves behind."""

import json

import pytest

from fwmanager.cli import main as cli
from fwmanager.errors import FwError
from fwmanager.i18n import set_language


@pytest.fixture(autouse=True)
def english():
    set_language(None)
    yield
    set_language(None)


def test_help_lists_only_commands_people_use(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "SUPPRESS" not in out and "dispatch" not in out
    for cmd in ("status", "profiles", "apply", "confirm", "rollback", "test-policy", "networks"):
        assert cmd in out


def test_no_command_shows_the_help(capsys):
    assert cli.main([]) == 2
    out = capsys.readouterr().out
    assert "COMMAND" in out and "cmd" not in out


def test_a_typo_does_not_reveal_the_internal_command(capsys):
    with pytest.raises(SystemExit):
        cli.main(["bogus"])
    err = capsys.readouterr().err
    assert "invalid choice" in err and "dispatch" not in err


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["dispatch", "eth0", "up"], True),
        (["--bus-address", "unix:path=/x", "dispatch", "eth0", "up"], True),
        (["--bus-address", "dispatch", "status"], False),  # "dispatch" there is a value
        (["status"], False),
        ([], False),
    ],
)
def test_dispatch_is_recognised_before_the_main_parser(argv, expected):
    assert cli._is_dispatch(argv) is expected


NAMES = ["home", "work", "public", "custom"]


@pytest.mark.parametrize(
    "typed,name",
    [("home", "home"), ("CASA", "home"), ("home", "home"), ("Home", "home"),
     ("work", "work"), ("public", "public"), ("Público", "public"),
     ("PUBLICO", "public"), ("custom", "custom"), (" casa ", "home"),
     ("nope", "nope"), ("", "")],
)  # fmt: skip
def test_profiles_can_be_typed_in_either_language(typed, name):
    assert cli.resolve_profile(typed, NAMES) == name


def test_portuguese_titles_are_understood_in_portuguese():
    set_language("pt")
    try:
        assert cli.resolve_profile("Trabalho", NAMES) == "work"
        assert cli.resolve_profile("Personalizado", NAMES) == "custom"
    finally:
        set_language(None)


class Recorder:
    def __init__(self, rollback_error=None):
        self.calls, self.rollback_error = [], rollback_error

    def apply_profile(self, name):
        self.calls.append(("apply", name))
        raise KeyboardInterrupt  # Ctrl+C while the service is working

    def rollback(self):
        self.calls.append(("rollback",))
        if self.rollback_error:
            raise self.rollback_error


def test_ctrl_c_undoes_a_change_this_command_started(capsys):
    client = cli._Tracking(Recorder())
    with pytest.raises(KeyboardInterrupt):
        client.apply_profile("home")
    assert cli._cancelled(client) == 130
    assert client._client.calls == [("apply", "home"), ("rollback",)]
    assert "Previous rules restored" in capsys.readouterr().err


def test_ctrl_c_never_undoes_someone_elses_change(capsys):
    client = cli._Tracking(Recorder())  # e.g. a --static test while the window has a change
    assert cli._cancelled(client) == 130
    assert client._client.calls == []


def test_ctrl_c_after_the_change_was_already_undone(capsys):
    client = cli._Tracking(Recorder(FwError("x", code="no_pending")))
    with pytest.raises(KeyboardInterrupt):
        client.apply_profile("home")
    assert cli._cancelled(client) == 130
    assert capsys.readouterr().err.strip() == "Cancelled."


def test_errors_say_what_to_do_next(monkeypatch, capsys):
    class Refusing:
        def __init__(self, _address):
            raise FwError("no", code="access_denied")

    monkeypatch.setattr(cli, "Client", Refusing)
    assert cli.main(["status"]) == 1
    err = capsys.readouterr().err
    assert "not allowed" in err and "usermod -aG fwmanager" in err


def test_profiles_are_shown_and_typed_in_the_persons_language(tmp_path):
    from pathlib import Path

    from fwmanager import i18n_build

    assert [cli._profile_label(n) for n in NAMES] == ["Home", "Work", "Public", "Custom"]
    assert [cli.command_name(n) for n in NAMES] == ["home", "work", "public", "custom"]
    assert cli.command_name("mine") == "mine"  # a name nobody translated stays as it is
    po = Path(__file__).resolve().parents[2] / "po"
    i18n_build.main(["compile", str(po), str(tmp_path)])
    set_language("pt", str(tmp_path))
    try:
        assert [cli.command_name(n) for n in NAMES] == [
            "casa", "trabalho", "publico", "personalizado"]  # fmt: skip
        assert cli.resolve_profile("home", NAMES) == "home"  # English still works in Portuguese
    finally:
        set_language(None)


def test_old_portuguese_ids_and_names_still_resolve_in_english():
    # Profile ids were Portuguese before; scripts typing them keep working in any language.
    for typed, name in (("casa", "home"), ("Trabalho", "work"), ("PÚBLICO", "public"),
                        ("publico", "public"), ("personalizado", "custom")):  # fmt: skip
        assert cli.resolve_profile(typed, NAMES) == name


# -- export, import and the routers of a network ---


class Backup:
    """The calls export / import / networks make, without a service."""

    def __init__(self):
        self.calls, self.exported = [], '{"format": "fwmanager-settings", "version": 1}\n'
        self.routers = [("Casa WiFi", "", ["aa:bb:cc:00:11:22"], "")]
        self.nets = [("Casa WiFi", "", "home")]

    def export_settings(self):
        return self.exported

    def import_settings(self, text):
        self.calls.append(("import", text))

    def status(self):
        return {"active_profile": "home"}

    def list_networks(self):
        return self.nets

    def list_network_routers(self):
        return self.routers

    def trust_router(self, ssid, uuid):
        self.calls.append(("trust", ssid, uuid))

    def forget_routers(self, ssid, uuid):
        self.calls.append(("forget", ssid, uuid))


def run_cli(argv, client):
    return cli.run(cli._parser().parse_args(argv), client)


def test_export_writes_a_private_file(tmp_path, capsys):
    target = tmp_path / "out.json"
    assert run_cli(["export", str(target)], Backup()) == 0
    assert target.read_text().startswith('{"format"')
    assert target.stat().st_mode & 0o077 == 0
    assert "Settings saved to" in capsys.readouterr().out


def test_export_to_the_screen(capsys):
    assert run_cli(["export", "-"], Backup()) == 0
    assert capsys.readouterr().out == Backup().exported


def test_export_to_an_unwritable_place_says_so(tmp_path, capsys):
    assert run_cli(["export", str(tmp_path / "nope" / "x.json")], Backup()) == 1
    assert "Could not write" in capsys.readouterr().err


def test_import_with_yes_replaces_and_says_what_next(tmp_path, capsys):
    source = tmp_path / "in.json"
    source.write_text("{}")
    client = Backup()
    assert run_cli(["import", str(source), "--yes"], client) == 0
    assert client.calls == [("import", "{}")]
    out = capsys.readouterr().out
    assert "Settings imported." in out and "fwmanager-cli apply home" in out


def test_import_asks_first_and_stops_on_no(tmp_path, monkeypatch, capsys):
    source = tmp_path / "in.json"
    source.write_text("{}")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "no")
    client = Backup()
    assert run_cli(["import", str(source)], client) == 130
    assert client.calls == [] and "Nothing was changed" in capsys.readouterr().out


def test_import_without_a_terminal_wants_yes(tmp_path, monkeypatch, capsys):
    source = tmp_path / "in.json"
    source.write_text("{}")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    client = Backup()
    assert run_cli(["import", str(source)], client) == 1
    assert client.calls == [] and "use --yes" in capsys.readouterr().err


def test_import_refuses_what_it_cannot_read(tmp_path, capsys):
    binary = tmp_path / "x.bin"
    binary.write_bytes(b"\xff\xfe\x00")
    client = Backup()
    for path in (tmp_path / "missing.json", binary):
        assert run_cli(["import", str(path), "--yes"], client) == 1
    assert client.calls == [] and "Could not read" in capsys.readouterr().err


def test_networks_show_the_router_state(capsys):
    client = Backup()
    client.routers = [("Casa WiFi", "", ["aa:bb:cc:00:11:22"], "aa:bb:cc:00:11:99")]
    run_cli(["networks", "list"], client)
    out = capsys.readouterr().out
    assert "aa:bb:cc:00:11:99" in out
    assert "fwmanager-cli networks trust-router --ssid 'Casa WiFi'" in out


def test_networks_json_carries_the_routers(capsys):
    run_cli(["networks", "list", "--json"], Backup())
    (item,) = json.loads(capsys.readouterr().out)
    assert item["routers"] == ["aa:bb:cc:00:11:22"] and item["unrecognised_router"] == ""


def test_trusting_and_forgetting_routers(capsys):
    client = Backup()
    run_cli(["networks", "trust-router", "--ssid", "Casa WiFi"], client)
    run_cli(["networks", "forget-router", "--uuid", "u-1"], client)
    assert client.calls == [("trust", "Casa WiFi", ""), ("forget", "", "u-1")]
