import pytest

from fwmanager.i18n import set_language
from fwmanager.ui import main as ui_main


@pytest.fixture(autouse=True)
def english():
    set_language(None)
    yield
    set_language(None)


@pytest.mark.parametrize("version", [(1, 5), (1, 6), (2, 0)])
def test_new_enough_is_fine(version):
    assert ui_main.runtime_problem(version) is None


@pytest.mark.parametrize("version", [(1, 2), (1, 4), (1, 0)])
def test_older_libadwaita_gets_an_explanation(version):
    text = ui_main.runtime_problem(version)
    assert "1.5" in text and f"{version[0]}.{version[1]}" in text
    assert "fwmanager-cli" in text  # the command line still works there


def test_missing_toolkit_gets_an_install_hint():
    assert "apt install gir1.2-gtk-4.0 gir1.2-adw-1" in ui_main.runtime_problem(None)


def test_the_real_toolkit_here_is_accepted():
    assert ui_main.runtime_problem(ui_main._adw_version()) is None


def test_refuses_to_run_as_root(monkeypatch, capsys):
    monkeypatch.setattr(ui_main.os, "geteuid", lambda: 0)
    assert ui_main.main([]) == 1
    assert "must not run as root" in capsys.readouterr().err
