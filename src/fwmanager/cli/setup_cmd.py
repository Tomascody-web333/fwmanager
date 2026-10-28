"""`fwmanager-cli setup` and `fwmanager-cli uninstall`: the window's setup and uninstall, in text.

Both use the same checks and the same privileged helper as the window, and the service records
when a profile was chosen. So setup done here is not asked for again in the window (or the
other way round), and one uninstall removes the window, the command line and the service.
"""

from __future__ import annotations

import os
import pwd
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

from fwmanager import setup_helper
from fwmanager.client import Client
from fwmanager.errors import FwError
from fwmanager.i18n import N_, _
from fwmanager.ui import privileged, relaunch, setup_state, state

EXIT_OK, EXIT_ERROR, EXIT_CANCELLED = 0, 1, 130


@dataclass
class Deps:
    """Everything that touches the machine, so tests can replace it."""

    client: Callable[[], Client]
    run_helper: Callable[[str], privileged.HelperResult] = lambda verb: privileged.run_setup_helper(
        verb=verb
    )
    group_info: Callable[[str], setup_state.GroupInfo] = lambda user: setup_state.group_info(
        user=user
    )
    ufw_present: Callable[[], bool] = setup_state.ufw_on_path
    relaunch: Callable[[list[str]], bool] = lambda args: relaunch.relaunch(
        relaunch.current_executable("fwmanager-cli"), args=args
    )
    ask: Callable[[str], str] = input
    interactive: Callable[[], bool] = lambda: sys.stdin.isatty()
    env: dict = field(default_factory=lambda: dict(os.environ))
    euid: Callable[[], int] = os.geteuid
    current_network: Callable[[], object] = lambda: _current_network()
    as_root_setup: Callable[[dict], int] = setup_helper.setup
    as_root_uninstall: Callable[[], int] = setup_helper.uninstall


def _current_network():
    from fwmanager.ui import netinfo

    return netinfo.current_network()


def _account(deps: Deps) -> str | None:
    """Who is being set up: the person, also when they typed `sudo`."""
    if deps.euid() == 0:
        uid = deps.env.get("SUDO_UID", "")
        if not uid.isdigit() or uid == "0":
            return None
        return pwd.getpwuid(int(uid)).pw_name
    return pwd.getpwuid(os.getuid()).pw_name


def _status(deps: Deps):
    try:
        client = deps.client()
        return client, client.status(), None
    except FwError as exc:
        return None, None, exc


def _requirements(deps: Deps, user: str):
    client, status, error = _status(deps)
    screen = state.classify(status, error)
    group = deps.group_info(user)
    if deps.euid() == 0:
        # Under sudo this process is root and may talk to the service; what matters is whether
        # the person is listed in the group, and they will have it at their next login.
        group = setup_state.GroupInfo(group.exists, group.listed, group.listed)
    rows = setup_state.evaluate(screen, status, group, deps.ufw_present(), user)
    return client, status, rows


def _print_rows(rows) -> None:
    for row in rows:
        mark = "✓" if row.ok else ("…" if row.state == setup_state.UNKNOWN else "✗")
        print(f"  {mark} {row.title}")


def _run_helper(deps: Deps, verb: str, user: str | None) -> privileged.HelperResult:
    """pkexec asks for the password in the terminal; under sudo there is nothing to ask."""
    if deps.euid() == 0:
        try:
            if verb == "setup":
                code = deps.as_root_setup({"PKEXEC_UID": deps.env["SUDO_UID"]})
            else:
                code = deps.as_root_uninstall()
        except setup_helper.Refused:
            code = setup_helper.EXIT_REFUSED
        return privileged.HelperResult(code)
    return deps.run_helper(verb)


def _helper_failed(result: privileged.HelperResult, manual: str) -> int:
    if result.dismissed:
        print(_("Cancelled. Nothing was changed."), file=sys.stderr)
        return EXIT_CANCELLED
    if result.not_authorised:
        print(_("Not authorised. Ask an administrator, or use the command below."), file=sys.stderr)
    elif not result.available:
        print(_("The password window is not available. Use the command below."), file=sys.stderr)
    else:
        print(_("The setup step failed. Try the commands shown instead."), file=sys.stderr)
    print("  " + manual, file=sys.stderr)
    return EXIT_ERROR


def setup(deps: Deps, profile: str | None = None, yes: bool = False) -> int:
    user = _account(deps)
    if user is None:
        print(_("Run it as your normal user: it asks for the password itself."), file=sys.stderr)
        return EXIT_ERROR

    print(_("Checking your system"))
    client, status, rows = _requirements(deps, user)
    _print_rows(rows)
    by_id = {r.id: r for r in rows}

    blocked = [r for r in rows if r.fix == setup_state.FIX_COMMAND and not r.ok]
    if blocked:
        print(_("Run this, then run fwmanager-cli setup again:"))
        for row in blocked:
            print(f"  {row.command}")
        return EXIT_ERROR

    if any(r.fix == setup_state.FIX_HELPER and not r.ok for r in rows):
        print(_("An administrator password is asked once."))
        result = _run_helper(deps, "setup", user)
        if not result.ok:
            manual = " && ".join(r.command for r in rows if r.command and not r.ok)
            return _helper_failed(result, manual)
        client, status, rows = _requirements(deps, user)
        by_id = {r.id: r for r in rows}

    if by_id[setup_state.ACCOUNT].state == setup_state.RESTART:
        # The group was just given: only a new process has it. Carry on in one.
        args = ["setup", *(["--profile", profile] if profile else []), *(["--yes"] if yes else [])]
        if not deps.relaunch(args):
            print(_("Please log out and back in, then run fwmanager-cli setup again."))
            return EXIT_ERROR
        return EXIT_OK

    if not setup_state.ready(rows) or client is None or status is None:
        _print_rows(rows)
        print(_("Something is still missing; see above."), file=sys.stderr)
        return EXIT_ERROR

    if status.get("configured"):
        active = state.profile_title(status.get("active_profile", "")) or _("none")
        print(_("fwmanager is already set up. Profile in use: {p}").format(p=active))
        return EXIT_OK
    return _choose_profile(deps, client, profile, yes)


def _choose_profile(deps: Deps, client, profile: str | None, yes: bool) -> int:
    from fwmanager.cli.main import _interactive_apply, _profile_label, resolve_profile

    names = client.list_profiles()
    if profile is None:
        if not deps.interactive():
            print(_("No terminal to ask in: use --profile NAME --yes."), file=sys.stderr)
            return EXIT_ERROR
        titles = dict(client.list_services())
        print("\n" + _("How should this computer behave on this network?"))
        for name in [n for n in state.PROFILE_ORDER if n in names]:
            print(f"  {_profile_label(name)}: {state.profile_hint(name)}")
            print("      " + state.describe_profile(client.get_profile(name), titles))
        answer = deps.ask(_("Profile [{default}]: ").format(default=state.profile_title("public")))
        profile = answer.strip() or "public"
    profile = resolve_profile(profile, names)
    net = deps.current_network()
    warning = state.open_wifi_warning(net, profile)
    if warning:
        print(_("Warning: {text}").format(text=warning))
    if yes:
        client.apply_profile(profile)
        client.confirm()
        print(_("Applied."))
        code = EXIT_OK
    else:
        code = _interactive_apply(client, profile)
    if code == EXIT_OK:
        _remember(deps, client, net, profile, yes)
        print(_("You are all set. fwmanager switches profile by itself when you change network."))
    return code


def _remember(deps: Deps, client, net, profile: str, yes: bool) -> None:
    """Like the window: this network gets this profile from now on (unless they say no)."""
    if net is None:
        return
    if not yes and deps.interactive():
        question = _("Remember {profile} for this network ({network})? [Y/n] ").format(
            profile=state.profile_title(profile), network=state.network_text(net)
        )
        if deps.ask(question).strip().lower() in ("n", "no", _("n"), _("no")):
            return
    client.set_network_profile(net.ssid, net.uuid, profile)
    print(_("Remembered."))


UNINSTALL_POINTS = (  # the same sentences as the window's uninstall dialog
    N_("fwmanager's own rules and its ping/mDNS settings are removed."),
    N_("ufw's original default settings are put back."),
    N_("ufw is switched off only if fwmanager was the one that switched it on."),
    N_("Rules you added to ufw yourself are kept."),
    N_(
        "The window, the command line and the background service are removed, "
        "for every account on this computer."
    ),
)
YES = N_("yes")


def uninstall(deps: Deps, yes: bool = False) -> int:
    print(_("Uninstall fwmanager"))
    for point in UNINSTALL_POINTS:
        print(f"  - {_(point)}")
    if not yes:
        if not deps.interactive():
            print(_("No terminal to ask in: use --yes."), file=sys.stderr)
            return EXIT_ERROR
        answer = deps.ask(_("Type yes to uninstall: "))
        if answer.strip().lower() not in ("yes", _(YES)):
            print(_("Cancelled. Nothing was changed."))
            return EXIT_CANCELLED
    if deps.euid() != 0:
        print(_("An administrator password is asked once."))
    result = _run_helper(deps, "uninstall", None)
    if not result.ok:
        return _helper_failed(result, "sudo /usr/local/libexec/fwmanager/uninstall")
    print(_("fwmanager was removed"))
    return EXIT_OK
