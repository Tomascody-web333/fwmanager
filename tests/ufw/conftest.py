"""Real-ufw tests: an isolated copy of /etc/ufw, enforced in a private network namespace."""

import pytest

from fwmanager.core.backend import UfwBackend
from fwmanager.core.services import load_builtin
from fwmanager.core.ufw_runner import Ufw


@pytest.fixture
def backend(ufw_root, netns_prefix, nft):
    """UfwBackend whose `ufw` binary runs inside the throw-away netns."""
    wrapper = ufw_root / "ufw-in-ns"
    wrapper.write_text("#!/bin/sh\nexec " + " ".join(netns_prefix) + ' /usr/sbin/ufw "$@"\n')
    wrapper.chmod(0o755)
    b = UfwBackend(Ufw(str(wrapper), rootdir=ufw_root), load_builtin())
    yield b  # `nft` flushes the namespace (iptables-nft lives in nftables) before and after
