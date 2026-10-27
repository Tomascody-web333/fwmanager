"""dev-install.sh / dev-uninstall.sh.

Staged tests (--root) are always safe. The "real" tests change the machine they run on
(group, /usr/local, /etc) so they only run when FWMANAGER_TEST_REAL_INSTALL=1, in a
throw-away VM/container.
"""

import grp
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "scripts" / "dev-install.sh"
UNINSTALL = ROOT / "scripts" / "dev-uninstall.sh"


def sh(*args, prefix=(), **kw):
    return subprocess.run(
        [*prefix, "sh", *map(str, args)], capture_output=True, text=True, timeout=60, **kw
    )


@pytest.fixture
def staged(tmp_path):
    return tmp_path / "root"


def stage(staged, *extra):
    return sh(INSTALL, "--user", "alice", "--root", staged, *extra)


def test_staged_install_places_every_file(staged):
    r = stage(staged, "--lang", "pt")
    assert r.returncode == 0, r.stderr
    expect = [
        "usr/local/bin/fwmanagerd",
        "usr/local/bin/fwmanager-cli",
        "usr/local/lib/fwmanager/fwmanager/daemon/main.py",
        "usr/local/lib/fwmanager/fwmanager/data/services.toml",
        "etc/dbus-1/system.d/org.fwmanager.Control.conf",
        "etc/systemd/system/fwmanagerd.service",
        "etc/fwmanager/config.toml",
        "etc/NetworkManager/dispatcher.d/90-fwmanager",
    ]
    for rel in expect:
        assert (staged / rel).is_file(), rel
    assert (staged / "etc/fwmanager/config.toml").read_text() == 'lang = "pt"\n'
    for exe in ("fwmanagerd", "fwmanager-cli"):
        assert os.access(staged / "usr/local/bin" / exe, os.X_OK)
    shim = staged / "etc/NetworkManager/dispatcher.d/90-fwmanager"
    assert os.access(shim, os.X_OK)  # NetworkManager only runs executable scripts
    assert (staged / "etc/systemd/system/fwmanagerd.service").read_text() == (
        ROOT / "data/systemd/fwmanagerd.service"
    ).read_text()
    assert not list((staged / "usr/local/lib").rglob("__pycache__"))


def test_installed_files_are_not_group_or_world_writable(staged):
    stage(staged)
    for p in staged.rglob("*"):
        assert not (p.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH)), p


def test_wrapper_ignores_python_environment(staged):
    stage(staged)
    text = (staged / "usr/local/bin/fwmanagerd").read_text()
    assert " -E -c " in text and "PYTHONPATH" not in text


def test_no_config_without_lang(staged):
    stage(staged)
    assert not (staged / "etc/fwmanager").exists()


def test_reinstall_is_idempotent(staged):
    assert stage(staged, "--lang", "en").returncode == 0
    assert stage(staged, "--lang", "pt").returncode == 0
    assert (staged / "etc/fwmanager/config.toml").read_text() == 'lang = "pt"\n'


def test_staged_uninstall_leaves_nothing(staged):
    stage(staged, "--lang", "en")
    r = sh(UNINSTALL, "--root", staged)
    assert r.returncode == 0, r.stderr
    leftovers = [p for p in staged.rglob("*") if p.is_file()]
    assert leftovers == []


@pytest.mark.parametrize(
    "args,msg",
    [
        ([], "--user NAME is required"),
        (["--user", "root"], "refusing to use root"),
        (["--user", 'a"b'], "invalid user name"),
        (["--user", "a b"], "invalid user name"),
        (["--user", "-rf"], "needs a value"),
        (["--user", "alice", "--lang", "fr"], "--lang must be pt or en"),
        (["--user", "alice", "--bogus"], "unknown argument"),
        (["--user"], "needs a value"),
    ],
)
def test_argument_validation(args, msg, staged):
    r = sh(INSTALL, *args, "--root", staged)
    assert r.returncode != 0 and msg in r.stderr
    assert not staged.exists() or not any(staged.rglob("*"))


def test_real_run_requires_existing_user():
    if os.geteuid() != 0:
        pytest.skip("needs root")
    r = sh(INSTALL, "--user", "no_such_user_xyz", "--no-systemd")
    assert r.returncode != 0 and "does not exist" in r.stderr


def test_real_run_rejects_non_root_caller():
    if os.geteuid() == 0:
        pytest.skip("already root")
    r = sh(INSTALL, "--user", "alice")
    assert r.returncode != 0 and "must run as root" in r.stderr


real = pytest.mark.skipif(
    os.environ.get("FWMANAGER_TEST_REAL_INSTALL") != "1" or os.geteuid() != 0,
    reason="set FWMANAGER_TEST_REAL_INSTALL=1 (as root, in a throw-away VM/container)",
)


@real
def test_real_install_and_uninstall_roundtrip(netns_prefix, tmp_path):
    if not shutil.which("ufw"):
        pytest.skip("ufw is not installed")
    if any(g.gr_name == "fwmanager" for g in grp.getgrall()):
        pytest.skip("group fwmanager already exists on this machine")
    user = "nobody"
    env = {"PATH": "/usr/bin:/bin"}
    try:
        r = sh(INSTALL, "--user", user, "--lang", "pt", "--no-systemd")
        assert r.returncode == 0, r.stderr
        members = next(g.gr_mem for g in grp.getgrall() if g.gr_name == "fwmanager")
        assert user in members and "root" in members  # root: the NM dispatcher is a client too
        shim = "/etc/NetworkManager/dispatcher.d/90-fwmanager"
        assert os.access(shim, os.X_OK)

        # The installed wrappers must really run.
        for exe in ("fwmanager-cli", "fwmanagerd"):
            h = subprocess.run(
                [f"/usr/local/bin/{exe}", "--help"],
                capture_output=True, text=True, timeout=30, env=env,
            )  # fmt: skip
            assert h.returncode == 0 and "usage" in h.stdout.lower(), h.stderr
        # The installed copy can clean a ufw tree (relocated, so the real /etc/ufw is untouched).
        root = tmp_path / "ufwroot"
        (root / "etc/default").mkdir(parents=True)
        (root / "lib").mkdir()
        shutil.copytree("/etc/ufw", root / "etc/ufw")
        shutil.copy("/etc/default/ufw", root / "etc/default/ufw")
        shutil.copytree("/lib/ufw", root / "lib/ufw")
        c = subprocess.run(
            ["/usr/local/bin/fwmanagerd", "--cleanup", f"--ufw-rootdir={root}",
             f"--state-dir={tmp_path / 'state'}"],
            capture_output=True, text=True, timeout=60, env=env,
        )  # fmt: skip
        assert c.returncode == 0 and c.stdout.strip() == "cleaned", c.stderr

        r = sh(UNINSTALL, "--no-systemd", prefix=netns_prefix)
        assert r.returncode == 0, r.stderr
        assert "Warning" not in r.stdout + r.stderr
        assert not any(g.gr_name == "fwmanager" for g in grp.getgrall())
        for path in (
            "/usr/local/bin/fwmanagerd",
            "/usr/local/bin/fwmanager-cli",
            "/usr/local/lib/fwmanager",
            "/etc/fwmanager",
            "/etc/dbus-1/system.d/org.fwmanager.Control.conf",
            "/etc/systemd/system/fwmanagerd.service",
            shim,
        ):
            assert not os.path.exists(path), path
    finally:  # never leave the test machine half-installed
        sh(UNINSTALL, "--no-systemd", prefix=netns_prefix)
        if shutil.which("groupdel"):
            subprocess.run(["groupdel", "fwmanager"], capture_output=True)
