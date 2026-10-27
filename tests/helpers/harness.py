"""Shared helpers for integration tests that run the real daemon."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from fwmanager.client import Client
from fwmanager.core import snapshot as snap
from fwmanager.errors import FwError

ROOT = Path(__file__).resolve().parents[2]


class Daemon:
    def __init__(self, prefix, bus, group="root", timeout=30, state_dir=None, extra=()):
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        self.state_dir = state_dir
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
                *extra,
            ],
            env=env,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.bus = bus
        for _ in range(100):
            try:
                self.client = Client(bus)
                self.client.list_profiles()
                return
            except FwError:
                time.sleep(0.05)
        self.proc.kill()
        raise RuntimeError("daemon did not start: " + self.proc.stderr.read())

    def stop(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(5)
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


def _zero_counters(node):
    """Packet/byte counters change with traffic; rules are what we compare."""
    if isinstance(node, dict):
        if set(node) == {"packets", "bytes"}:
            return {"packets": 0, "bytes": 0}
        return {k: _zero_counters(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_zero_counters(v) for v in node]
    return node


def snapshot_of(nft):
    """Handle- and counter-free listing of the fwmanager table (None if absent)."""
    return _zero_counters(snap.normalize(nft.list_table_json("inet", "fwmanager")))


def table_exists(nft):
    return nft.list_table_json("inet", "fwmanager") is not None


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
        timeout=60,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), **(env or {})},
    )
