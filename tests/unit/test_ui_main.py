import os
import subprocess
import sys
from pathlib import Path

import pytest

from fwmanager.i18n import set_language
from fwmanager.ui import main as ui_main

SRC = str(Path(__file__).resolve().parents[2] / "src")


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
    # In a child process: importing GTK with no display would spoil it for the UI tests.
    code = "from fwmanager.ui import main as m; print(m.runtime_problem(m._adw_version()))"
    env = {**os.environ, "PYTHONPATH": SRC}
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, env=env
    )
    assert out.stdout.strip() == "None", out.stderr


def test_without_a_graphical_session_it_explains_instead_of_crashing(monkeypatch, capsys):
    monkeypatch.setattr(ui_main.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(ui_main, "_adw_version", lambda: (1, 5))
    monkeypatch.setattr(ui_main, "_can_open_windows", lambda: False)
    assert ui_main.main([]) == 1
    assert "graphical session" in capsys.readouterr().err


def test_the_real_app_without_a_display_says_why(tmp_path):
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "PYTHONPATH": SRC, "LANG": "C"}
    out = subprocess.run(
        [sys.executable, "-m", "fwmanager.ui.main"], capture_output=True, text=True,
        timeout=60, env=env, user=65534 if os.geteuid() == 0 else None,
    )  # fmt: skip
    assert out.returncode == 1 and "Traceback" not in out.stderr, out.stderr
    assert "graphical session" in out.stderr


def test_refuses_to_run_as_root(monkeypatch, capsys):
    monkeypatch.setattr(ui_main.os, "geteuid", lambda: 0)
    assert ui_main.main([]) == 1
    assert "must not run as root" in capsys.readouterr().err
