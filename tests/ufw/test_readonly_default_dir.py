"""The systemd unit lets the service write /etc/default/ufw but not the rest of /etc/default.

Reproduced here with a private mount namespace: the directory is bind-mounted read-only and the
one file read-write on top, exactly what ReadWritePaths does. Real ufw and fwmanager must both
manage with that.
"""

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

SCRIPT = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from fwmanager.core.backend import Options, UfwBackend
    from fwmanager.core.profiles import DEFAULT_PROFILES
    from fwmanager.core.services import load_builtin
    from fwmanager.core.ufw_runner import Ufw

    root, wrapper = Path(sys.argv[1]), sys.argv[2]
    probe = root / "etc/default/.probe"
    try:
        probe.write_text("x")
        sys.exit("the directory is writable: the test set-up is wrong")
    except OSError:
        pass
    b = UfwBackend(Ufw(wrapper, rootdir=root), load_builtin())
    before = (root / "etc/default/ufw").read_bytes()
    snap = b.snapshot()
    b.apply(DEFAULT_PROFILES["home"], Options())  # sets deny incoming in /etc/default/ufw
    assert (root / "etc/default/ufw").read_bytes() != before, "nothing was written"
    b.restore(snap)
    assert (root / "etc/default/ufw").read_bytes() == before, "not restored"
    print("ok")
    """
)


def test_service_works_with_only_the_default_file_writable(ufw_root, netns_prefix, nft, tmp_path):
    if not shutil.which("unshare") or not shutil.which("mount"):
        pytest.skip("needs unshare and mount")
    wrapper = ufw_root / "ufw-in-ns"
    wrapper.write_text("#!/bin/sh\nexec " + " ".join(netns_prefix) + ' /usr/sbin/ufw "$@"\n')
    wrapper.chmod(0o755)
    d, f = ufw_root / "etc/default", ufw_root / "etc/default/ufw"
    # Start from an open input policy, so applying a profile has to rewrite the file.
    f.write_text(
        f.read_text().replace('DEFAULT_INPUT_POLICY="DROP"', 'DEFAULT_INPUT_POLICY="ACCEPT"')
    )
    assert 'DEFAULT_INPUT_POLICY="ACCEPT"' in f.read_text()
    (tmp_path / "check.py").write_text(SCRIPT)
    shell = (
        f"mount --make-rprivate / && mount --bind {d} {d} && mount -o remount,bind,ro {d} {d}"
        f" && mount --bind {f} {f} && mount -o remount,bind,rw {f} {f}"
        f" && exec {sys.executable} {tmp_path / 'check.py'} {ufw_root} {wrapper}"
    )
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    r = subprocess.run(
        ["unshare", "-m", "sh", "-c", shell], capture_output=True, text=True, timeout=180, env=env
    )
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stdout + r.stderr
