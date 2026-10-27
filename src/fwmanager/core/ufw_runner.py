"""Runs the ``ufw`` binary with a fixed argument list (never a shell, never client text)."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from fwmanager.errors import FwError

UFW_TIMEOUT = 60


class UfwError(FwError):
    code = "ufw_failed"


class UfwMissing(FwError):
    code = "ufw_missing"


class Ufw:
    """``rootdir`` relocates ufw's config/state (``ufw --rootdir``); used by tests."""

    def __init__(self, binary: str = "ufw", rootdir: Path | None = None) -> None:
        self._binary = binary
        self.rootdir = Path(rootdir) if rootdir else None

    @property
    def config_dir(self) -> Path:
        return (self.rootdir or Path("/")) / "etc" / "ufw"

    @property
    def default_file(self) -> Path:
        return (self.rootdir or Path("/")) / "etc" / "default" / "ufw"

    def run(self, args: Sequence[str]) -> str:
        cmd = [self._binary]
        if self.rootdir:
            cmd += [f"--rootdir={self.rootdir}", f"--datadir={self.rootdir}"]
        env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C"}
        try:
            proc = subprocess.run(
                [*cmd, *args],
                capture_output=True,
                text=True,
                timeout=UFW_TIMEOUT,
                check=False,
                env=env,
            )
        except FileNotFoundError as exc:
            raise UfwMissing("ufw is not installed") from exc
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UfwError(f"cannot run ufw: {exc}") from exc
        if proc.returncode != 0:
            raise UfwError((proc.stderr or proc.stdout).strip()[:300] or "ufw failed")
        return proc.stdout

    def installed(self) -> bool:
        try:
            self.run(["version"])
        except UfwMissing:
            return False
        except UfwError:
            return True
        return True

    def is_active(self) -> bool:
        return self.run(["status"]).lstrip().startswith("Status: active")

    def ipv6_enabled(self) -> bool:
        """ufw filters IPv6 only if IPV6=yes *and* the kernel has IPv6."""
        try:
            text = self.default_file.read_text()
        except OSError:
            return False
        flag = any(
            ln.split("#")[0].replace(" ", "").lower() == "ipv6=yes" for ln in text.splitlines()
        )
        return flag and os.path.exists("/proc/net/if_inet6")
