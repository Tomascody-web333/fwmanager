"""The only privileged steps of the app, run through ``pkexec`` (polkit asks for a password).

Two verbs, no other arguments:

- ``setup``: add the account that asked to the ``fwmanager`` group and start the service. The
  account is never read from the command line: ``pkexec`` puts the caller's uid in
  ``PKEXEC_UID`` and nothing else is trusted.
- ``uninstall``: run the installed uninstaller, which undoes fwmanager's changes to ufw and
  removes the app (window, command line and service alike).

Everything it runs is a fixed argument list with an absolute path.
"""

from __future__ import annotations

import os
import pwd
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

GROUP = "fwmanager"
SERVICE = "fwmanagerd.service"
UNINSTALLER = Path("/usr/local/libexec/fwmanager/uninstall")
USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}\$?$")

# Silent when it works (the window and the CLI say it in the person's language); a failure is
# explained on stderr.
EXIT_OK, EXIT_FAILED, EXIT_REFUSED, EXIT_USAGE = 0, 1, 3, 64

TOOLS = {
    "groupadd": ("/usr/sbin/groupadd", "/sbin/groupadd"),
    "usermod": ("/usr/sbin/usermod", "/sbin/usermod"),
    "systemctl": ("/usr/bin/systemctl", "/bin/systemctl"),
}


class Refused(Exception):
    """The request is not something this helper will do."""


def _tool(name: str) -> str:
    for path in TOOLS[name]:
        if Path(path).exists():
            return path
    raise Refused(f"{name} not found")


def caller(env: Mapping[str, str], lookup: Callable = pwd.getpwuid) -> str:
    """The account that ran ``pkexec``. Only ``PKEXEC_UID`` is believed."""
    raw = env.get("PKEXEC_UID", "")
    if not (raw.isascii() and raw.isdigit()):  # "²" and "١٠٠٠" are digits to str.isdigit
        raise Refused("must be started through pkexec")
    uid = int(raw)
    if uid == 0:
        raise Refused("root does not need this")
    try:
        name = lookup(uid).pw_name
    except KeyError:
        raise Refused("unknown account") from None
    if not USERNAME_RE.fullmatch(name):
        raise Refused("unsupported account name")
    return name


def group_exists(group_file: Path = Path("/etc/group")) -> bool:
    try:
        return any(line.split(":", 1)[0] == GROUP for line in group_file.read_text().splitlines())
    except OSError:
        return False


def setup(
    env: Mapping[str, str],
    run: Callable = subprocess.run,
    lookup: Callable = pwd.getpwuid,
    has_group: Callable[[], bool] = group_exists,
) -> int:
    """Allow the calling account and start the service. Safe to repeat."""
    user = caller(env, lookup)
    steps = []
    if not has_group():
        steps.append([_tool("groupadd"), "--system", GROUP])
    steps.append([_tool("usermod"), "-aG", GROUP, user])
    steps.append([_tool("systemctl"), "enable", "--now", SERVICE])
    for argv in steps:
        result = run(argv, capture_output=True, text=True, timeout=60, check=False)
        if result.returncode != 0:
            print(f"fwmanager-setup: {Path(argv[0]).name} failed", file=sys.stderr)
            return EXIT_FAILED
    return EXIT_OK


def uninstall(
    run: Callable = subprocess.run,
    uninstaller: Path = UNINSTALLER,
    euid: Callable[[], int] = os.geteuid,
) -> int:
    """Remove fwmanager for everyone. Its changes to ufw are undone first, by the uninstaller."""
    if euid() != 0:
        raise Refused("must run as root (through pkexec)")
    if not uninstaller.is_file():
        raise Refused("uninstaller not found")
    # Everything this process needs is already loaded: the uninstaller deletes our own files.
    result = run(["/bin/sh", str(uninstaller)], capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        print("fwmanager-setup: the uninstaller failed", file=sys.stderr)
        print(result.stderr[-2000:], file=sys.stderr)
        return EXIT_FAILED
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv not in (["setup"], ["uninstall"]):
        print("usage: fwmanager-setup setup|uninstall   (run it through pkexec)", file=sys.stderr)
        return EXIT_USAGE
    try:
        return setup(os.environ) if argv == ["setup"] else uninstall()
    except Refused as exc:
        print(f"fwmanager-setup: refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
