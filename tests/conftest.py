import os
import shutil
import subprocess

import pytest

from fwmanager.core.nft import Nft


@pytest.fixture(scope="session")
def netns_prefix():
    """Command prefix that runs a program inside a private, throw-away network namespace."""
    if os.geteuid() != 0 or not all(shutil.which(b) for b in ("nft", "unshare", "nsenter")):
        pytest.skip("integration tests need root, nft, unshare and nsenter")
    holder = subprocess.Popen(["unshare", "-n", "sleep", "infinity"])
    # Wait until the child has really unshared its namespace.
    mine = os.readlink("/proc/self/ns/net")
    for _ in range(100):
        try:
            if os.readlink(f"/proc/{holder.pid}/ns/net") != mine:
                break
        except OSError:
            pass
        subprocess.run(["sleep", "0.02"])
    else:
        holder.kill()
        pytest.skip("cannot create a network namespace")
    yield ["nsenter", "-t", str(holder.pid), "-n"]
    holder.kill()
    holder.wait()


@pytest.fixture
def nft(netns_prefix):
    """Real nft bound to the private namespace, starting from an empty ruleset."""
    n = Nft(prefix=netns_prefix)
    n.run(["flush", "ruleset"])
    yield n
    n.run(["flush", "ruleset"])
