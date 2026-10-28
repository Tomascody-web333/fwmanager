"""The running window carries the app id, so desktops match it to its launcher and icon."""

import ctypes
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.ui
SRC = str(Path(__file__).resolve().parents[2] / "src")
# Exactly how the installed launcher starts it: `python -I -c ...` (root check bypassed here).
LAUNCH = (
    "import os, sys; sys.path.insert(0, sys.argv[1]); os.geteuid = lambda: 1000; "
    "from fwmanager.ui.main import main; sys.exit(main([]))"
)


def window_classes(display):
    found = subprocess.run(
        ["xdotool", "search", "--name", "^fwmanager$"], capture_output=True, text=True
    ).stdout.split()
    x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
    x11.XOpenDisplay.restype = ctypes.c_void_p

    class Hint(ctypes.Structure):
        _fields_ = [("res_name", ctypes.c_char_p), ("res_class", ctypes.c_char_p)]

    dpy = x11.XOpenDisplay(display.encode())
    classes = []
    for wid in found:
        hint = Hint()
        if x11.XGetClassHint(ctypes.c_void_p(dpy), ctypes.c_ulong(int(wid)), ctypes.byref(hint)):
            classes.append((hint.res_name or b"").decode())
    x11.XCloseDisplay(ctypes.c_void_p(dpy))
    return classes


def test_the_window_class_is_the_app_id(display, tmp_path):
    if not shutil.which("xdotool"):
        pytest.skip("xdotool is not installed")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "DISPLAY": os.environ["DISPLAY"],
           "GDK_BACKEND": "x11"}  # fmt: skip
    proc = subprocess.Popen(
        [sys.executable, "-I", "-c", LAUNCH, SRC], env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        end, classes = time.monotonic() + 20, []
        while time.monotonic() < end and "org.fwmanager.App" not in classes:
            time.sleep(0.3)
            classes = window_classes(os.environ["DISPLAY"])
        assert "org.fwmanager.App" in classes, classes
        assert "-c" not in classes
    finally:
        proc.kill()
        proc.wait()
