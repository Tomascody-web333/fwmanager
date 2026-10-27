"""A private dbus-daemon that enforces the *real* system-bus defaults plus our policy file.

Built from /usr/share/dbus-1/system.conf (deny own, deny method calls by default) with the
daemon-only bits removed. Our policy is included with the group renamed to one that exists on
the test machine, so no system group has to be created.
"""

import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SYSTEM_CONF = Path("/usr/share/dbus-1/system.conf")

DROP_TAGS = (
    r"<user>.*?</user>",
    r"<fork\s*/>",
    r"<syslog\s*/>",
    r"<pidfile>.*?</pidfile>",
    r"<servicehelper>.*?</servicehelper>",
    r"<standard_system_servicedirs\s*/>",
    r"<listen>.*?</listen>",
    r"<include[^>]*>.*?</include>",
    r"<includedir>.*?</includedir>",
)


def policy_with_group(group: str) -> str:
    text = (ROOT / "data/dbus/org.fwmanager.Control.conf").read_text()
    assert 'group="fwmanager"' in text
    return text.replace('group="fwmanager"', f'group="{group}"')


def start(group: str = "nogroup"):
    """Returns (address, stop). Needs dbus-daemon and the system.conf of this machine."""
    if not SYSTEM_CONF.exists() or not shutil.which("dbus-daemon"):
        raise FileNotFoundError("no system.conf or dbus-daemon")
    d = Path(tempfile.mkdtemp(prefix="fwm-sysbus-"))
    d.chmod(0o755)
    sock = d / "bus.sock"
    policy = d / "fwmanager.conf"
    policy.write_text(policy_with_group(group))
    text = SYSTEM_CONF.read_text()
    for tag in DROP_TAGS:
        text = re.sub(tag, "", text, flags=re.S)
    text = text.replace("</busconfig>", f"<listen>unix:path={sock}</listen>\n"
                        f"<include>{policy}</include>\n</busconfig>")  # fmt: skip
    conf = d / "system-like.conf"
    conf.write_text(text)
    proc = subprocess.Popen(["dbus-daemon", f"--config-file={conf}", "--nofork"],
                            stderr=subprocess.DEVNULL)  # fmt: skip
    for _ in range(100):
        if sock.exists():
            break
        time.sleep(0.05)
    sock.chmod(0o777)

    def stop():
        proc.terminate()
        proc.wait()
        shutil.rmtree(d, ignore_errors=True)

    return f"unix:path={sock}", stop
