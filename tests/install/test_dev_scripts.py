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


def sh(*args, prefix=(), lang="C", **kw):
    env = {**os.environ, "LANG": lang}
    env.pop("LC_ALL", None)
    env.pop("SSH_CONNECTION", None)
    return subprocess.run(
        [*prefix, "sh", *map(str, args)], capture_output=True, text=True, timeout=60, env=env, **kw
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
        "usr/local/bin/fwmanager",
        "usr/share/locale/pt_PT/LC_MESSAGES/fwmanager.mo",
        "usr/share/locale/en/LC_MESSAGES/fwmanager.mo",
        "usr/share/applications/org.fwmanager.App.desktop",
        "usr/local/libexec/fwmanager/fwmanager-setup",
        "usr/share/polkit-1/actions/org.fwmanager.setup.policy",
    ]
    for rel in expect:
        assert (staged / rel).is_file(), rel
    assert (staged / "etc/fwmanager/config.toml").read_text() == 'lang = "pt"\n'
    for exe in ("fwmanagerd", "fwmanager-cli", "fwmanager"):
        assert os.access(staged / "usr/local/bin" / exe, os.X_OK)
    assert os.access(staged / "usr/local/libexec/fwmanager/fwmanager-setup", os.X_OK)
    shim = staged / "etc/NetworkManager/dispatcher.d/90-fwmanager"
    assert os.access(shim, os.X_OK)  # NetworkManager only runs executable scripts
    assert (staged / "etc/systemd/system/fwmanagerd.service").read_text() == (
        ROOT / "data/systemd/fwmanagerd.service"
    ).read_text()
    assert not list((staged / "usr/local/lib").rglob("__pycache__"))


def test_installed_catalogue_is_a_valid_portuguese_translation(staged):
    import gettext

    stage(staged)
    mo = staged / "usr/share/locale/pt_PT/LC_MESSAGES/fwmanager.mo"
    tr = gettext.GNUTranslations(mo.open("rb"))
    assert tr.gettext("Settings") == "Definições" and tr.charset() == "UTF-8"
    assert not list(staged.rglob("*.po")) and not list(staged.rglob("*.pot"))


def test_desktop_entry_is_well_formed(staged):
    stage(staged)
    text = (staged / "usr/share/applications/org.fwmanager.App.desktop").read_text()
    assert text.startswith("[Desktop Entry]") and "Exec=fwmanager" in text
    assert "Categories=" in text


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


def test_user_is_optional_because_setup_happens_in_the_window(staged):
    r = sh(INSTALL, "--root", staged)
    assert r.returncode == 0, r.stderr
    assert "setup continues in its window" in r.stdout


def test_messages_follow_the_system_language(staged):
    pt = sh(INSTALL, "--root", staged, lang="pt_PT.UTF-8")
    assert "a configuração continua na janela" in pt.stdout
    bad = sh(INSTALL, "--root", staged, "--bogus", lang="pt_PT.UTF-8")
    assert "argumento desconhecido" in bad.stderr
    out = sh(UNINSTALL, "--root", staged, lang="pt_PT.UTF-8")
    assert "fwmanager removido" in out.stdout
    en = sh(UNINSTALL, "--root", staged, lang="en_GB.UTF-8")
    assert "fwmanager removed" in en.stdout


def test_polkit_policy_matches_the_installed_helper(staged):
    import xml.etree.ElementTree as ET

    stage(staged)
    policy = staged / "usr/share/polkit-1/actions/org.fwmanager.setup.policy"
    root = ET.parse(policy).getroot()
    action = root.find("action")
    assert action is not None and action.get("id") == "org.fwmanager.setup"
    paths = [
        a.text
        for a in action.iter("annotate")
        if a.get("key") == "org.freedesktop.policykit.exec.path"
    ]
    assert paths == ["/usr/local/libexec/fwmanager/fwmanager-setup"]
    if shutil.which("xmllint"):
        dtd = Path("/usr/share/polkit-1/policyconfig-1.dtd")
        if dtd.exists():
            r = subprocess.run(
                ["xmllint", "--noout", "--dtdvalid", str(dtd), str(policy)],
                capture_output=True, text=True,
            )  # fmt: skip
            assert r.returncode == 0, r.stderr


def test_installer_guards_against_ssh_lockout():
    text = INSTALL.read_text()
    assert "SSH_CONNECTION" in text and "--allow-ssh-lockout" in text


def test_reinstall_is_idempotent(staged):
    assert stage(staged, "--lang", "en").returncode == 0
    assert stage(staged, "--lang", "pt").returncode == 0
    assert (staged / "etc/fwmanager/config.toml").read_text() == 'lang = "pt"\n'


def test_staged_uninstall_leaves_nothing(staged):
    stage(staged, "--lang", "en")
    r = sh(UNINSTALL, "--root", staged)
    assert r.returncode == 0, r.stderr
    assert not (staged / "usr/local/libexec/fwmanager").exists()
    leftovers = [p for p in staged.rglob("*") if p.is_file()]
    assert leftovers == []


@pytest.mark.parametrize(
    "args,msg",
    [
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
        # The installed catalogue is used: Portuguese help via /etc/fwmanager/config.toml.
        pt = subprocess.run(
            ["/usr/local/bin/fwmanager-cli", "--help"],
            capture_output=True, text=True, timeout=30, env=env,
        )  # fmt: skip
        assert "Cliente do gestor de firewall" in pt.stdout, pt.stdout + pt.stderr
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
            "/usr/local/libexec/fwmanager",
            "/usr/share/polkit-1/actions/org.fwmanager.setup.policy",
        ):
            assert not os.path.exists(path), path
    finally:  # never leave the test machine half-installed
        sh(UNINSTALL, "--no-systemd", prefix=netns_prefix)
        if shutil.which("groupdel"):
            subprocess.run(["groupdel", "fwmanager"], capture_output=True)
