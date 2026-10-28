"""The .deb package (packaging/deb/build-deb.sh).

Building and inspecting it is always safe. Installing it changes the machine (dpkg database,
/usr, a system group), so those tests only run when FWMANAGER_TEST_REAL_INSTALL=1, as root, in a
throw-away VM/container.
"""

import grp
import hashlib
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "packaging" / "deb" / "build-deb.sh"
VERSION = next(
    line.split('"')[1]
    for line in (ROOT / "pyproject.toml").read_text().splitlines()
    if line.startswith("version = ")
)
DEB = f"fwmanager.{VERSION}.deb"


def can_build() -> bool:
    if not shutil.which("dpkg-deb"):
        return False
    probe = "import gi; gi.require_version('GdkPixbuf', '2.0')"
    for py in ("python3", "/usr/bin/python3.12", "/usr/bin/python3.11"):
        if shutil.which(py) and subprocess.run([py, "-I", "-c", probe]).returncode == 0:
            return True
    return False


pytestmark = pytest.mark.skipif(not can_build(), reason="needs dpkg-deb and PyGObject")


def build(out: Path, epoch: str = "1700000000") -> Path:
    env = {**os.environ, "SOURCE_DATE_EPOCH": epoch}
    r = subprocess.run(
        ["sh", str(BUILD), "--out", str(out)], capture_output=True, text=True, env=env, timeout=300
    )
    assert r.returncode == 0, r.stderr
    return out / DEB


@pytest.fixture(scope="module")
def deb(tmp_path_factory):
    return build(tmp_path_factory.mktemp("dist"))


@pytest.fixture(scope="module")
def tree(deb, tmp_path_factory):
    root = tmp_path_factory.mktemp("tree")
    subprocess.run(["dpkg-deb", "-R", str(deb), str(root)], check=True)
    return root


def field(deb: Path, name: str) -> str:
    return subprocess.run(
        ["dpkg-deb", "-f", str(deb), name], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_package_metadata(deb):
    assert field(deb, "Package") == "fwmanager"
    assert field(deb, "Version") == VERSION
    assert field(deb, "Architecture") == "all"
    depends = field(deb, "Depends")
    for dep in ("python3 (>= 3.11)", "python3-gi", "ufw", "pkexec | policykit-1"):
        assert dep in depends
    assert "gir1.2-adw-1 (>= 1.5)" in field(deb, "Recommends")  # the CLI works without it


def test_every_file_is_where_a_package_puts_it(tree):
    expect = [
        "usr/bin/fwmanager",
        "usr/bin/fwmanager-cli",
        "usr/bin/fwmanagerd",
        "usr/lib/fwmanager/fwmanager/daemon/main.py",
        "usr/lib/fwmanager/fwmanager/data/services.toml",
        "usr/libexec/fwmanager/fwmanager-setup",
        "usr/libexec/fwmanager/uninstall",
        "usr/lib/systemd/system/fwmanagerd.service",
        "usr/lib/NetworkManager/dispatcher.d/90-fwmanager",
        "usr/share/dbus-1/system.d/org.fwmanager.Control.conf",
        "usr/share/polkit-1/actions/org.fwmanager.setup.policy",
        "usr/share/applications/org.fwmanager.App.desktop",
        "usr/share/icons/hicolor/scalable/apps/org.fwmanager.App.svg",
        "usr/share/icons/hicolor/symbolic/apps/org.fwmanager.App-symbolic.svg",
        "usr/share/icons/hicolor/48x48/apps/org.fwmanager.App.png",
        "usr/share/pixmaps/org.fwmanager.App.png",
        "usr/share/fwmanager/locale/pt_PT/LC_MESSAGES/fwmanager.mo",
        "usr/share/fwmanager/locale/en/LC_MESSAGES/fwmanager.mo",
        "usr/share/doc/fwmanager/copyright",
    ]
    for rel in expect:
        assert (tree / rel).is_file(), rel
    for rel in (
        "usr/bin/fwmanager", "usr/bin/fwmanager-cli", "usr/bin/fwmanagerd",
        "usr/libexec/fwmanager/fwmanager-setup", "usr/libexec/fwmanager/uninstall",
        "usr/lib/NetworkManager/dispatcher.d/90-fwmanager",
        "DEBIAN/postinst", "DEBIAN/prerm", "DEBIAN/postrm",
    ):  # fmt: skip
        assert os.access(tree / rel, os.X_OK), rel
    assert not (tree / "usr/local").exists() and not (tree / "etc").exists()
    assert not list(tree.rglob("__pycache__")) and not list(tree.rglob("*.pyc"))


def test_everything_points_at_usr_not_usr_local(tree):
    code = (tree / "usr/lib/fwmanager/fwmanager/paths.py").read_text()
    assert 'PREFIX = "/usr"\n' in code
    assert 'LOCALEDIR = "/usr/share/fwmanager/locale"\n' in code
    assert "/usr/bin/fwmanagerd" in (tree / "usr/lib/systemd/system/fwmanagerd.service").read_text()
    shim = (tree / "usr/lib/NetworkManager/dispatcher.d/90-fwmanager").read_text()
    assert "exec /usr/bin/fwmanager-cli dispatch" in shim
    policy = ET.parse(tree / "usr/share/polkit-1/actions/org.fwmanager.setup.policy").getroot()
    paths = {a.text for a in policy.iter("annotate") if a.get("key").endswith(".exec.path")}
    assert paths == {"/usr/libexec/fwmanager/fwmanager-setup"}
    for rel in ("usr/bin", "usr/libexec", "usr/share/dbus-1", "usr/share/applications",
                "usr/lib/systemd", "usr/lib/NetworkManager", "DEBIAN"):  # fmt: skip
        for path in (tree / rel).rglob("*"):
            if path.is_file():
                assert "/usr/local" not in path.read_text(errors="replace"), path


def test_maintainer_scripts_are_valid_shell(tree):
    for name in ("postinst", "prerm", "postrm"):
        r = subprocess.run(["sh", "-n", str(tree / "DEBIAN" / name)], capture_output=True)
        assert r.returncode == 0, (name, r.stderr)
    uninstaller = tree / "usr/libexec/fwmanager/uninstall"
    assert subprocess.run(["sh", "-n", str(uninstaller)]).returncode == 0


def test_md5sums_cover_every_file(tree):
    listed = {line.split("  ", 1)[1] for line in (tree / "DEBIAN/md5sums").read_text().splitlines()}
    files = {
        str(p.relative_to(tree))
        for p in tree.rglob("*")
        if p.is_file() and p.relative_to(tree).parts[0] != "DEBIAN"
    }
    assert listed == files


def test_sha256sums_matches_the_package(deb):
    sums = (deb.parent / "SHA256SUMS").read_text()
    digest = hashlib.sha256(deb.read_bytes()).hexdigest()
    assert sums == f"{digest}  {DEB}\n"
    r = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=deb.parent, capture_output=True)
    assert r.returncode == 0, r.stdout


def test_the_build_is_reproducible(deb, tmp_path):
    again = build(tmp_path)
    assert again.read_bytes() == deb.read_bytes()


# -- installing it for real ---

real = pytest.mark.skipif(
    os.environ.get("FWMANAGER_TEST_REAL_INSTALL") != "1" or os.geteuid() != 0,
    reason="set FWMANAGER_TEST_REAL_INSTALL=1 (as root, in a throw-away VM/container)",
)

INSTALLED = (
    "/usr/bin/fwmanager",
    "/usr/bin/fwmanager-cli",
    "/usr/bin/fwmanagerd",
    "/usr/lib/fwmanager",
    "/usr/libexec/fwmanager",
    "/usr/lib/systemd/system/fwmanagerd.service",
    "/usr/lib/NetworkManager/dispatcher.d/90-fwmanager",
    "/usr/share/dbus-1/system.d/org.fwmanager.Control.conf",
    "/usr/share/polkit-1/actions/org.fwmanager.setup.policy",
    "/usr/share/applications/org.fwmanager.App.desktop",
    "/usr/share/icons/hicolor/scalable/apps/org.fwmanager.App.svg",
    "/usr/share/fwmanager/locale/pt_PT/LC_MESSAGES/fwmanager.mo",  # not /usr/share/locale:
)  # minimal Ubuntu images make dpkg skip that one, and the app would lose its Portuguese
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}


def installed() -> bool:
    r = subprocess.run(
        ["dpkg-query", "-W", "-f=${Status}", "fwmanager"], capture_output=True, text=True
    )
    return r.returncode == 0 and "installed" in r.stdout.split()


def group_members() -> list[str] | None:
    return next((g.gr_mem for g in grp.getgrall() if g.gr_name == "fwmanager"), None)


@pytest.fixture
def clean_machine():
    if not shutil.which("ufw"):
        pytest.skip("ufw is not installed")
    if installed() or group_members() is not None or Path("/usr/local/bin/fwmanagerd").exists():
        pytest.skip("fwmanager (or its group) is already on this machine")
    yield
    subprocess.run(["dpkg", "--purge", "fwmanager"], capture_output=True, env=ENV)
    subprocess.run(["groupdel", "fwmanager"], capture_output=True)


def ufw_files() -> dict:
    files = [p for p in Path("/etc/ufw").rglob("*") if p.is_file()] + [Path("/etc/default/ufw")]
    return {str(p): p.read_bytes() for p in files}


def dpkg(*args, prefix=()):
    r = subprocess.run(
        [*prefix, "dpkg", *args], capture_output=True, text=True, env=ENV, timeout=300
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return r


@real
def test_install_use_and_uninstall_from_the_app(deb, clean_machine, netns_prefix):
    """apt install ./fwmanager.deb, use the CLI, then the app's own Uninstall removes it."""
    before = ufw_files()
    dpkg("-i", str(deb))
    assert installed()
    for path in INSTALLED:
        assert os.path.exists(path), path
    assert "root" in group_members()  # root: the NetworkManager dispatcher is a client too
    assert os.stat("/var/lib/fwmanager").st_mode & 0o777 == 0o700

    cli = "/usr/bin/fwmanager-cli"
    env = {**ENV, "LANG": "en_GB.UTF-8"}
    h = subprocess.run([cli, "--help"], capture_output=True, text=True, env=env, timeout=30)
    assert h.returncode == 0 and "usage:" in h.stdout and "uninstall" in h.stdout, h.stderr
    pt = subprocess.run(
        [cli, "--help"], capture_output=True, text=True, env={**ENV, "LANG": "pt_PT.UTF-8"}
    )
    assert "Linha de comandos do fwmanager" in pt.stdout, pt.stdout + pt.stderr
    # Installing does not start the service (no lock-out over SSH): setup does that.
    s = subprocess.run([cli, "status"], capture_output=True, text=True, env=env, timeout=30)
    assert s.returncode == 1 and "systemctl enable --now fwmanagerd" in s.stderr
    d = subprocess.run(
        ["/usr/bin/fwmanagerd", "--help"], capture_output=True, text=True, env=env, timeout=30
    )
    assert d.returncode == 0, d.stderr

    u = subprocess.run(
        [*netns_prefix, cli, "uninstall", "--yes"],
        capture_output=True, text=True, env=env, timeout=300,
    )  # fmt: skip
    assert u.returncode == 0, u.stdout + u.stderr
    assert "fwmanager was removed" in u.stdout
    assert not installed()
    for path in (*INSTALLED, "/var/lib/fwmanager", "/etc/fwmanager"):
        assert not os.path.exists(path), path
    assert group_members() is None  # the package made the group, so purge removes it
    assert ufw_files() == before  # nothing of ours was there: ufw is exactly as it was


@real
def test_remove_keeps_choices_and_purge_deletes_them(deb, clean_machine, netns_prefix):
    dpkg("-i", str(deb))
    state = Path("/var/lib/fwmanager/state.json")
    state.write_text('{"version": 1, "profile": "home"}')
    dpkg("--remove", "fwmanager", prefix=netns_prefix)
    assert not os.path.exists("/usr/bin/fwmanager-cli")
    assert not os.path.exists("/usr/lib/fwmanager")  # no compiled leftovers either
    assert state.exists() and group_members() is not None  # a reinstall picks them up
    dpkg("-i", str(deb))
    assert state.exists()
    dpkg("--purge", "fwmanager", prefix=netns_prefix)
    assert not state.parent.exists() and group_members() is None


@real
def test_upgrade_in_place_keeps_everything(deb, clean_machine, tmp_path):
    dpkg("-i", str(deb))
    Path("/var/lib/fwmanager/state.json").write_text('{"version": 1, "profile": "work"}')
    dpkg("-i", str(deb))  # the same version again stands in for an upgrade
    assert installed() and Path("/var/lib/fwmanager/state.json").exists()
    assert "root" in group_members()


# -- the source package Launchpad builds (debian/) ---


def control_fields(text: str) -> dict:
    fields, key = {}, None
    for line in text.splitlines():
        if line[:1] in (" ", "\t") and key:
            fields[key] += " " + line.strip()
        elif ":" in line:
            key, value = line.split(":", 1)
            fields[key] = value.strip()
    return fields


def test_debian_changelog_has_the_released_version():
    first = (ROOT / "debian/changelog").read_text().splitlines()[0]
    assert first.startswith(f"fwmanager ({VERSION}) ")


def test_both_packages_ask_for_the_same_things():
    ours = control_fields((ROOT / "packaging/deb/control.in").read_text())
    binary = (ROOT / "debian/control").read_text().split("\n\n")[1]
    theirs = control_fields(binary)
    assert theirs["Depends"].replace(", ${misc:Depends}", "") == ours["Depends"]
    for name in ("Recommends", "Architecture", "Description"):
        assert theirs[name] == ours[name], name


def test_maintainer_scripts_live_once_in_debian():
    for name in ("postinst", "prerm", "postrm"):
        text = (ROOT / "debian" / name).read_text()
        assert text.startswith("#!/bin/sh") and "#DEBHELPER#" in text
        assert not (ROOT / "packaging/deb" / name).exists()


@pytest.mark.skipif(
    not (shutil.which("dpkg-buildpackage") and shutil.which("dh")), reason="needs debhelper"
)
def test_launchpad_style_build_holds_the_same_program(deb, tmp_path):
    src = tmp_path / f"fwmanager-{VERSION}"
    files = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    for rel in files:
        if (ROOT / rel).is_file():
            (src / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, src / rel)
    r = subprocess.run(
        ["dpkg-buildpackage", "-us", "-uc", "-b"], cwd=src, capture_output=True, text=True,
        timeout=600,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    built = tmp_path / f"fwmanager_{VERSION}_all.deb"

    def program(path):
        listing = subprocess.run(
            ["dpkg-deb", "-c", str(path)], capture_output=True, text=True, check=True
        ).stdout
        names = {line.split()[-1] for line in listing.splitlines()}
        return {n for n in names if "/usr/share/doc/" not in n}

    assert program(built) == program(deb)
