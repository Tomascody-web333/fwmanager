import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from helpers.harness import make_ufw_root
from helpers.nftcli import NftCli

ROOT = Path(__file__).resolve().parents[1]

# On CI (FWMANAGER_TEST_NO_SKIPS=1) every tool is installed, so a skipped test means something
# is missing there, not that it cannot run: it fails instead. Only these skips are expected.
EXPECTED_SKIPS = ("already root",)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if os.environ.get("FWMANAGER_TEST_NO_SKIPS") != "1" or not report.skipped:
        return
    if hasattr(report, "wasxfail"):
        return
    reason = report.longrepr[-1] if isinstance(report.longrepr, tuple) else str(report.longrepr)
    if not any(expected in reason for expected in EXPECTED_SKIPS):
        report.outcome = "failed"
        report.longrepr = f"skipped on CI, where it should run: {reason}"


def _readable_by_others(path: Path) -> bool:
    return all(os.stat(p).st_mode & 0o005 == 0o005 for p in [path, *path.parents])


@pytest.fixture(scope="session")
def src_for_others():
    """The source tree where an unprivileged account (nobody) can read it.

    A CI checkout often sits under a home folder others cannot enter; then a copy is used.
    """
    src = ROOT / "src"
    if _readable_by_others(src):
        yield src
        return
    copy = Path(tempfile.mkdtemp(prefix="fwm-src-"))
    shutil.copytree(src / "fwmanager", copy / "fwmanager",
                    ignore=shutil.ignore_patterns("__pycache__"))  # fmt: skip
    for path in [copy, *copy.rglob("*")]:
        path.chmod(0o755 if path.is_dir() else 0o644)
    yield copy
    shutil.rmtree(copy)


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
    n = NftCli(netns_prefix)
    n.run(["flush", "ruleset"])
    yield n
    n.run(["flush", "ruleset"])


@pytest.fixture(scope="session")
def private_bus():
    """A throw-away dbus-daemon (never the system bus) that lets any local user connect."""
    if not shutil.which("dbus-daemon"):
        pytest.skip("dbus-daemon not available")
    # Not under pytest's tmp dir: that one is 0700 and "nobody" must reach the socket.
    d = Path(tempfile.mkdtemp(prefix="fwm-bus-"))
    d.chmod(0o755)
    sock = d / "bus.sock"
    conf = d / "bus.conf"
    conf.write_text(
        f"""<busconfig>
  <type>custom</type>
  <listen>unix:path={sock}</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow user="*"/>
    <allow own="*"/>
    <allow send_destination="*"/>
    <allow receive_sender="*"/>
    <allow send_type="method_call"/>
    <allow send_type="method_return"/>
    <allow send_type="error"/>
    <allow send_type="signal"/>
  </policy>
</busconfig>"""
    )
    proc = subprocess.Popen(["dbus-daemon", f"--config-file={conf}", "--nofork"])
    for _ in range(100):
        if sock.exists():
            break
        subprocess.run(["sleep", "0.05"])
    sock.chmod(0o777)
    yield f"unix:path={sock}"
    proc.terminate()
    proc.wait()
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="session")
def ufw_available():
    if not shutil.which("ufw"):
        pytest.skip("ufw is not installed")


@pytest.fixture
def ufw_root(tmp_path, ufw_available):
    return make_ufw_root(tmp_path)
