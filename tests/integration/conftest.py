import pytest
from helpers.harness import Daemon


@pytest.fixture
def daemon(nft, netns_prefix, private_bus, tmp_path, ufw_root):
    """Real daemon in a private netns with a private ufw tree. First boot already ran."""
    d = Daemon(netns_prefix, private_bus, ufw_root, timeout=3, state_dir=tmp_path / "state")
    yield d
    d.stop()
