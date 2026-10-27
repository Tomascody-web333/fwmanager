"""Real-ufw tests: an isolated copy of /etc/ufw, enforced in a private network namespace."""

import shutil

import pytest

from fwmanager.core.backend import UfwBackend
from fwmanager.core.services import load_builtin
from fwmanager.core.ufw_runner import Ufw


@pytest.fixture(scope="session")
def ufw_available():
    if not shutil.which("ufw"):
        pytest.skip("ufw is not installed")


@pytest.fixture
def ufw_root(tmp_path, ufw_available):
    """A private copy of ufw's pristine config; the real /etc/ufw is never touched."""
    root = tmp_path / "ufwroot"
    (root / "etc/default").mkdir(parents=True)
    (root / "lib").mkdir()
    shutil.copytree("/etc/ufw", root / "etc/ufw")
    shutil.copy("/etc/default/ufw", root / "etc/default/ufw")
    shutil.copytree("/lib/ufw", root / "lib/ufw")
    return root


@pytest.fixture
def backend(ufw_root, netns_prefix, nft):
    """UfwBackend whose `ufw` binary runs inside the throw-away netns."""
    wrapper = ufw_root / "ufw-in-ns"
    wrapper.write_text("#!/bin/sh\nexec " + " ".join(netns_prefix) + ' /usr/sbin/ufw "$@"\n')
    wrapper.chmod(0o755)
    b = UfwBackend(Ufw(str(wrapper), rootdir=ufw_root), load_builtin())
    yield b  # `nft` flushes the namespace (iptables-nft lives in nftables) before and after
