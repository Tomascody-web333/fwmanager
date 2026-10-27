"""GTK tests run against a virtual X server (Xvfb) and a fake daemon client."""

import os
import shutil
import subprocess
import time

import pytest


@pytest.fixture(scope="session", autouse=True)
def display():
    if not shutil.which("Xvfb"):
        pytest.skip("Xvfb is not installed")
    proc = subprocess.Popen(
        ["Xvfb", ":97", "-screen", "0", "1280x900x24", "-nolisten", "tcp"],
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.8)
    saved = {k: os.environ.get(k) for k in ("DISPLAY", "GDK_BACKEND", "GSK_RENDERER")}
    os.environ.update({"DISPLAY": ":97", "GDK_BACKEND": "x11", "GSK_RENDERER": "cairo"})
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    proc.terminate()
    proc.wait()


@pytest.fixture(scope="session")
def gtk(display):
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw

    Adw.init()
    return True
