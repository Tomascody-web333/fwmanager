"""Shared helpers for integration tests that run the real daemon against real ufw."""

import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from fwmanager.client import Client
from fwmanager.core import ufw_files
from fwmanager.errors import FwError

ROOT = Path(__file__).resolve().parents[2]
START_TIMEOUT = 40  # first boot enables ufw, which takes a few seconds


def make_ufw_root(base: Path) -> Path:
    """A private copy of ufw's pristine config; the real /etc/ufw is never touched."""
    root = base / "ufwroot"
    (root / "etc/default").mkdir(parents=True)
    (root / "lib").mkdir()
    shutil.copytree("/etc/ufw", root / "etc/ufw")
    shutil.copy("/etc/default/ufw", root / "etc/default/ufw")
    shutil.copytree("/lib/ufw", root / "lib/ufw")
    return root


def iptables_save(prefix):
    out = subprocess.run(
        [*prefix, "iptables-save"], capture_output=True, text=True, check=True
    ).stdout
    keep = [ln for ln in out.splitlines() if not ln.startswith("#")]
    return [re.sub(r"\[\d+:\d+\]", "[0:0]", ln) for ln in keep]


def fingerprint(ufw_root: Path, prefix):
    """Everything a rollback must reproduce: ufw's files, and the kernel rules if ufw is on."""
    files = {rel: (ufw_root / rel).read_bytes() for rel in ufw_files.SNAPSHOT_FILES}
    enabled = b"ENABLED=yes" in files["etc/ufw/ufw.conf"]
    return files, (iptables_save(prefix) if enabled else None)


def ufw_cmd(ufw_root: Path, prefix, *args):
    """Run the real ufw (relocated tree) inside the namespace, like an admin would."""
    return subprocess.run(
        [*prefix, "ufw", f"--rootdir={ufw_root}", f"--datadir={ufw_root}", *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
        env={**os.environ, "LC_ALL": "C"},
    ).stdout


class Daemon:
    def __init__(self, prefix, bus, ufw_root, group="root", timeout=30, state_dir=None, extra=()):
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        self.state_dir = state_dir
        self.ufw_root = ufw_root
        self.prefix = list(prefix)
        self.proc = subprocess.Popen(
            [
                *prefix,
                sys.executable,
                "-m",
                "fwmanager.daemon.main",
                "--bus-address",
                bus,
                "--group",
                group,
                "--confirm-timeout",
                str(timeout),
                "--state-dir",
                str(state_dir or "/nonexistent-state"),
                "--ufw-rootdir",
                str(ufw_root),
                *extra,
            ],
            env=env,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.bus = bus
        end = time.monotonic() + START_TIMEOUT
        while time.monotonic() < end:
            if self.proc.poll() is not None:
                break
            try:
                self.client = Client(bus)
                self.client.list_profiles()
                self.initial = self.fp()
                return
            except FwError:
                time.sleep(0.1)
        self.proc.kill()
        raise RuntimeError("daemon did not start: " + self.proc.stderr.read())

    def fp(self):
        return fingerprint(self.ufw_root, self.prefix)

    def stop(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        return self.proc.stderr.read()


def wait_until(cond, seconds=5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def busctl_as(uid, bus, method, *args):
    return subprocess.run(
        [
            "busctl",
            f"--address={bus}",
            "call",
            "org.fwmanager.Control",
            "/org/fwmanager/Control",
            "org.fwmanager.Control",
            method,
            *args,
        ],
        capture_output=True,
        text=True,
        user=uid,
        timeout=15,
    )


def bring_lo_up(prefix):
    """Set the loopback of the (fresh) namespace UP; there is no `ip` tool in minimal images."""
    script = (
        "import fcntl, socket, struct\n"
        "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
        "fcntl.ioctl(s, 0x8914, struct.pack('16sH14x', b'lo', 0x1 | 0x40))\n"
    )
    subprocess.run([*prefix, sys.executable, "-c", script], check=True, timeout=10)


def run_cli_in_ns(prefix, bus, *args, env=None):
    """Run fwmanager-cli inside the namespace, against the daemon on the private bus."""
    return subprocess.run(
        [*prefix, sys.executable, "-m", "fwmanager.cli.main", "--bus-address", bus, *args],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), **(env or {})},
    )
