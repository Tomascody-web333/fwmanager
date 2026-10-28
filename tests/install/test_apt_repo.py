"""The signed APT repository (packaging/apt/build-repo.sh), with a throw-away signing key.

Building and checking it is always safe. Installing from it changes the machine, so that test
only runs when FWMANAGER_TEST_REAL_INSTALL=1, as root, in a throw-away VM/container.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from test_deb import DEB, build, can_build, group_members, installed

ROOT = Path(__file__).resolve().parents[2]
BUILD_REPO = ROOT / "packaging" / "apt" / "build-repo.sh"
ARCHES = ("all", "amd64", "arm64", "armhf", "i386")

pytestmark = pytest.mark.skipif(
    not (can_build() and shutil.which("apt-ftparchive") and shutil.which("gpg")),
    reason="needs dpkg-deb, PyGObject, apt-ftparchive (apt-utils) and gpg",
)


@pytest.fixture(scope="module")
def gnupg(tmp_path_factory):
    home = tmp_path_factory.mktemp("gnupg")
    home.chmod(0o700)
    env = {**os.environ, "GNUPGHOME": str(home)}
    subprocess.run(
        ["gpg", "--batch", "--quiet", "--passphrase", "secret", "--quick-gen-key",
         "fwmanager test <test@example.invalid>", "ed25519", "sign", "1d"],
        env=env, check=True, capture_output=True,
    )  # fmt: skip
    out = subprocess.run(
        ["gpg", "--batch", "--with-colons", "--fingerprint", "test@example.invalid"],
        env=env, check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    fingerprint = next(line.split(":")[9] for line in out.splitlines() if line.startswith("fpr:"))
    return env, fingerprint


def make_repo(gnupg, debs: Path, out: Path, passphrase="secret"):
    env, fingerprint = gnupg
    return subprocess.run(
        ["sh", str(BUILD_REPO), str(debs), str(out), fingerprint],
        env={**env, "APT_GPG_PASSPHRASE": passphrase}, capture_output=True, text=True,
        timeout=120,
    )  # fmt: skip


@pytest.fixture(scope="module")
def repo(gnupg, tmp_path_factory):
    debs = tmp_path_factory.mktemp("debs")
    build(debs)
    out = tmp_path_factory.mktemp("repo") / "site"
    r = make_repo(gnupg, debs, out)
    assert r.returncode == 0, r.stderr
    return out


def verify(repo: Path, tmp_path: Path, name="InRelease", *extra):
    keyring = tmp_path / "key.gpg"
    keyring.write_bytes(
        subprocess.run(
            ["gpg", "--batch", "--dearmor"], input=(repo / "fwmanager.asc").read_bytes(),
            capture_output=True, check=True,
        ).stdout
    )  # fmt: skip
    return subprocess.run(
        ["gpgv", "--keyring", str(keyring), *extra, str(repo / "dists/stable" / name)],
        capture_output=True, text=True,
    )  # fmt: skip


def test_layout(repo):
    assert (repo / "pool/main" / DEB).is_file()
    for arch in ARCHES:
        packages = (repo / f"dists/stable/main/binary-{arch}/Packages").read_text()
        assert "Package: fwmanager" in packages and f"Filename: pool/main/{DEB}" in packages
        assert (repo / f"dists/stable/main/binary-{arch}/Packages.gz").is_file()
    release = (repo / "dists/stable/Release").read_text()
    assert "Suite: stable" in release and "Components: main" in release
    assert "Architectures: all amd64 arm64 armhf i386" in release
    assert "SHA256:" in release
    assert "BEGIN PGP PUBLIC KEY BLOCK" in (repo / "fwmanager.asc").read_text()
    assert "PRIVATE" not in (repo / "fwmanager.asc").read_text()  # only the public half
    page = (repo / "index.html").read_text()
    assert "signed-by=/etc/apt/keyrings/fwmanager.asc" in page and "apt install fwmanager" in page


def test_signatures_verify(repo, tmp_path):
    assert verify(repo, tmp_path).returncode == 0
    r = subprocess.run(
        ["gpgv", "--keyring", str(tmp_path / "key.gpg"),
         str(repo / "dists/stable/Release.gpg"), str(repo / "dists/stable/Release")],
        capture_output=True,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr


def test_a_changed_index_no_longer_verifies(repo, tmp_path):
    copy = tmp_path / "copy"
    shutil.copytree(repo, copy)
    signed = copy / "dists/stable/InRelease"
    signed.write_text(signed.read_text().replace("Suite: stable", "Suite: stablE"))
    assert verify(copy, tmp_path).returncode != 0


def test_no_or_broken_packages_fail(gnupg, tmp_path):
    debs = tmp_path / "debs"
    debs.mkdir()
    r = make_repo(gnupg, debs, tmp_path / "out")
    assert r.returncode != 0 and "no .deb files" in r.stderr
    (debs / DEB).write_bytes(b"not a package")
    r = make_repo(gnupg, debs, tmp_path / "out2")
    assert r.returncode != 0 and "could not be read" in r.stderr


def test_a_wrong_passphrase_signs_nothing(gnupg, repo, tmp_path):
    env, _fingerprint = gnupg
    subprocess.run(["gpgconf", "--kill", "gpg-agent"], env=env, check=True)  # forget it
    r = make_repo(gnupg, repo / "pool/main", tmp_path / "out", passphrase="wrong")
    assert r.returncode != 0
    assert not (tmp_path / "out/dists/stable/InRelease").exists()


real = pytest.mark.skipif(
    os.environ.get("FWMANAGER_TEST_REAL_INSTALL") != "1" or os.geteuid() != 0,
    reason="set FWMANAGER_TEST_REAL_INSTALL=1 (as root, in a throw-away VM/container)",
)


@real
def test_apt_installs_from_the_signed_repository(repo, netns_prefix, tmp_path):
    if installed() or group_members() is not None or Path("/usr/local/bin/fwmanagerd").exists():
        pytest.skip("fwmanager (or its group) is already on this machine")
    # apt reads as its own unprivileged user: the files must be where everyone can read them.
    site = Path("/srv/fwmanager-apt-test")
    key = Path("/etc/apt/keyrings/fwmanager-apt-test.asc")
    listing = site / "test.list"
    shutil.rmtree(site, ignore_errors=True)
    shutil.copytree(repo, site / "repo")
    subprocess.run(["chmod", "-R", "a+rX", str(site)], check=True)
    key.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    shutil.copy(repo / "fwmanager.asc", key)
    key.chmod(0o644)
    listing.write_text(f"deb [signed-by={key}] file:{site / 'repo'} stable main\n")
    apt = ["-o", f"Dir::Etc::sourcelist={listing}", "-o", "Dir::Etc::sourceparts=/nonexistent",
           "-o", "APT::Get::List-Cleanup=0", "-q"]  # fmt: skip
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "DEBIAN_FRONTEND": "noninteractive"}
    try:
        u = subprocess.run(["apt-get", *apt, "update"], capture_output=True, text=True, env=env)
        assert u.returncode == 0 and "not signed" not in u.stdout + u.stderr, u.stdout + u.stderr
        i = subprocess.run(
            ["apt-get", *apt, "install", "-y", "fwmanager"], capture_output=True, text=True,
            env=env, timeout=300,
        )  # fmt: skip
        assert i.returncode == 0, i.stdout + i.stderr
        assert installed()
        # The same repository, but its index re-signed by someone else's key: apt refuses it.
        signed = site / "repo/dists/stable/InRelease"
        signed.write_text(signed.read_text().replace("Suite: stable", "Suite: stablE"))
        (site / "repo/dists/stable/Release.gpg").unlink()
        bad = subprocess.run(["apt-get", *apt, "update"], capture_output=True, text=True, env=env)
        assert "BADSIG" in bad.stdout + bad.stderr or bad.returncode != 0
    finally:
        subprocess.run(
            [*netns_prefix, "apt-get", "purge", "-y", "-q", "fwmanager"],
            capture_output=True, env=env, timeout=300,
        )  # fmt: skip
        subprocess.run(["groupdel", "fwmanager"], capture_output=True)
        shutil.rmtree(site, ignore_errors=True)
        key.unlink(missing_ok=True)
        lists = Path("/var/lib/apt/lists")
        for f in lists.glob("_srv_fwmanager-apt-test_*"):
            f.unlink()
    assert not installed()
