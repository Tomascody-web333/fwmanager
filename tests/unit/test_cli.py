"""fwmanager-cli without a daemon: help, names, and what Ctrl+C leaves behind."""

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


NAMES = ["casa", "trabalho", "publico", "personalizado"]


@pytest.mark.parametrize(
    "typed,name",
    [("casa", "casa"), ("CASA", "casa"), ("home", "casa"), ("Home", "casa"),
     ("work", "trabalho"), ("public", "publico"), ("Público", "publico"),
     ("PUBLICO", "publico"), ("custom", "personalizado"), (" casa ", "casa"),
     ("nope", "nope"), ("", "")],
)  # fmt: skip
def test_profiles_can_be_typed_in_either_language(typed, name):
    assert cli.resolve_profile(typed, NAMES) == name


def test_portuguese_titles_are_understood_in_portuguese():
    set_language("pt")
    try:
        assert cli.resolve_profile("Trabalho", NAMES) == "trabalho"
        assert cli.resolve_profile("Personalizado", NAMES) == "personalizado"
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
        client.apply_profile("casa")
    assert cli._cancelled(client) == 130
    assert client._client.calls == [("apply", "casa"), ("rollback",)]
    assert "Previous rules restored" in capsys.readouterr().err


def test_ctrl_c_never_undoes_someone_elses_change(capsys):
    client = cli._Tracking(Recorder())  # e.g. a --static test while the window has a change
    assert cli._cancelled(client) == 130
    assert client._client.calls == []


def test_ctrl_c_after_the_change_was_already_undone(capsys):
    client = cli._Tracking(Recorder(FwError("x", code="no_pending")))
    with pytest.raises(KeyboardInterrupt):
        client.apply_profile("casa")
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
