"""fwmanager-cli: command-line client for the fwmanager daemon."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import unicodedata

from fwmanager.cli import argparse_text
from fwmanager.client import Client
from fwmanager.core.config import SYSTEM_CONFIG, resolve_language
from fwmanager.core.services import load_catalog
from fwmanager.errors import FwError
from fwmanager.i18n import N_, _, set_language
from fwmanager.messages import describe_error, describe_finding, describe_probe, finding_detail
from fwmanager.ui import state
from fwmanager.ui.settings import settings_path

EXIT_OK, EXIT_ERROR, EXIT_REVERTED = 0, 1, 2
EXIT_INTERRUPTED = 130  # the shell's convention for Ctrl+C

# What to do next, after the error itself.
HINTS = {
    "unknown_profile": N_("See the names with: fwmanager-cli profiles"),
    "invalid_profile": N_("See the names with: fwmanager-cli profiles"),
    "access_denied": N_(
        "Open fwmanager from the applications menu to set up your account, "
        "or ask an administrator to run: sudo usermod -aG fwmanager YOUR_USER"
    ),
    "daemon_unavailable": N_("Start it with: sudo systemctl enable --now fwmanagerd"),
}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fwmanager-cli",
        description=_(
            "Command line for fwmanager, a friendly interface for the ufw firewall. "
            "A change is undone after 30 seconds unless you confirm it."
        ),
        epilog=_(
            "Profiles can be named in English or Portuguese (home, casa, Público...). "
            "Exit codes: 0 done, 1 error, 2 change undone or warnings, 3 unsafe, 130 cancelled."
        ),
    )
    p.add_argument("--bus-address", help=argparse.SUPPRESS)  # development/tests only
    sub = p.add_subparsers(dest="cmd", title=_("commands"), metavar=_("COMMAND"))

    sp = sub.add_parser("setup", help=_("set fwmanager up (the same as the window's first run)"))
    sp.add_argument("--profile", metavar=_("PROFILE"), help=_("profile to use, without asking"))
    sp.add_argument("--yes", action="store_true", help=_("confirm immediately, no countdown"))

    sp = sub.add_parser("status", help=_("show the firewall state"))
    sp.add_argument("--json", action="store_true", help=_("machine-readable output"))

    sub.add_parser("profiles", help=_("list the profiles and what each one lets in"))

    sp = sub.add_parser("apply", help=_("switch to a profile (undone unless you confirm)"))
    sp.add_argument("profile", metavar=_("PROFILE"), help=_("profile name"))
    sp.add_argument("--yes", action="store_true", help=_("confirm immediately, no countdown"))

    sp = sub.add_parser("edit", help=_("change what a profile allows and blocks"))
    sp.add_argument("profile", metavar=_("PROFILE"), help=_("profile name"))
    sp.add_argument(
        "--allow", nargs="+", default=[], metavar=_("SERVICE"), help=_("services to let in")
    )
    sp.add_argument(
        "--block", nargs="+", default=[], metavar=_("SERVICE"), help=_("services to block")
    )
    sp.add_argument("--ping", choices=["on", "off"], help=_("answer ping"))
    sp.add_argument("--mdns", choices=["on", "off"], help=_("be visible on the local network"))
    sp.add_argument(
        "--from",
        dest="source",
        metavar=_("WHO"),
        help=_("who may connect: anywhere, lan, or networks such as 192.168.1.0/24,10.0.0.0/8"),
    )

    sp = sub.add_parser("reset", help=_("put a profile back to its defaults"))
    sp.add_argument("profile", metavar=_("PROFILE"), help=_("profile name"))

    sub.add_parser("confirm", help=_("keep the change that is waiting for confirmation"))
    sub.add_parser("rollback", help=_("undo the change that is waiting for confirmation"))

    sp = sub.add_parser("test-policy", help=_("check that a profile will not break the machine"))
    sp.add_argument("profile", metavar=_("PROFILE"), help=_("profile name"))
    sp.add_argument(
        "--static", action="store_true", help=_("only analyse the rules, change nothing")
    )

    sp = sub.add_parser(
        "uninstall", help=_("remove fwmanager and undo its changes to the firewall")
    )
    sp.add_argument("--yes", action="store_true", help=_("do not ask for confirmation"))

    sp = sub.add_parser("networks", help=_("which profile to use on which network"))
    nsub = sp.add_subparsers(dest="net_cmd", required=True, metavar=_("ACTION"))
    nsp = nsub.add_parser("list", help=_("show the remembered networks"))
    nsp.add_argument("--json", action="store_true", help=_("machine-readable output"))
    for name, text in (
        ("set", _("use a profile on a network")),
        ("remove", _("forget a network")),
    ):
        nsp = nsub.add_parser(name, help=text)
        nsp.add_argument("--ssid", default="", help=_("Wi-Fi name"))
        nsp.add_argument("--uuid", default="", help=_("NetworkManager connection UUID"))
        if name == "set":
            nsp.add_argument("profile", metavar=_("PROFILE"), help=_("profile name"))

    return p


def _dispatch_parser() -> argparse.ArgumentParser:
    """``fwmanager-cli dispatch IFACE ACTION``: run by NetworkManager, never by people.

    Kept out of the main parser so it shows up in no help text or error message.
    """
    p = argparse.ArgumentParser(prog="fwmanager-cli dispatch")
    p.add_argument("--bus-address")
    p.add_argument("cmd", choices=["dispatch"])
    p.add_argument("iface")
    p.add_argument("action")
    return p


def _is_dispatch(argv: list[str]) -> bool:
    words = [a for i, a in enumerate(argv) if i == 0 or argv[i - 1] != "--bus-address"]
    positional = [a for a in words if not a.startswith("-")]
    return positional[:1] == ["dispatch"]


def _fold(text: str) -> str:
    """Case and accents do not matter: "Público" == "publico"."""
    decomposed = unicodedata.normalize("NFKD", text.strip().casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def resolve_profile(arg: str, names: list[str]) -> str:
    """Map what a person typed to a profile name; unknown input is passed on unchanged."""
    wanted = _fold(arg)
    for name in names:
        english = state.PROFILE_TITLES.get(name, name)
        if wanted in {_fold(name), _fold(english), _fold(state.profile_title(name))}:
            return name
    return arg


def _profile_label(name: str) -> str:
    title = state.profile_title(name)
    return name if title == name else f"{title} ({name})"


class _Tracking:
    """The client, remembering whether this command started a change (so Ctrl+C can undo it)."""

    def __init__(self, client: Client) -> None:
        self._client = client
        self.started = False

    def apply_profile(self, name: str) -> None:
        self.started = True  # set first: the request is sent even if we are interrupted
        self._client.apply_profile(name)

    def __getattr__(self, attr):
        return getattr(self._client, attr)


def _interactive_apply(client: Client, name: str) -> int:
    from gi.repository import GLib

    loop = GLib.MainLoop()
    result = {"code": EXIT_OK}

    def on_signal(signal: str, _args: tuple) -> None:
        if signal == "Reverted":
            result["code"] = EXIT_REVERTED
            loop.quit()

    def on_stdin(fd, _cond) -> bool:
        data = os.read(fd, 256)
        if not data:  # EOF (no terminal): never counts as a confirmation
            return False
        try:
            if data.decode(errors="replace").strip().lower() in ("r", "n"):
                client.rollback()
            else:
                client.confirm()
                print("\n" + _("Confirmed."))
                loop.quit()
        except FwError as exc:
            print("\n" + _("Error: {msg}").format(msg=describe_error(exc)), file=sys.stderr)
            loop.quit()
        return False

    def tick() -> bool:
        st = client.status()
        if not st["pending_profile"]:
            return False
        print(
            "\r"
            + _("Press Enter to confirm, r to revert ({n}s left)... ").format(n=st["seconds_left"]),
            end="",
            flush=True,
        )
        return True

    client.subscribe(on_signal)
    client.apply_profile(name)
    GLib.io_add_watch(sys.stdin.fileno(), GLib.IO_IN, on_stdin)
    GLib.timeout_add_seconds(1, tick)
    tick()
    loop.run()
    if result["code"] == EXIT_REVERTED:
        print("\n" + _("Previous rules restored."))
    return result["code"]


EXIT_WARNINGS, EXIT_UNSAFE = 2, 3
VERDICT_TEXT = {"safe": N_("SAFE"), "warnings": N_("SAFE WITH WARNINGS"), "unsafe": N_("UNSAFE")}
SEVERITY_TEXT = {"error": N_("error"), "warning": N_("warning"), "info": N_("info")}


def _test_policy(client: Client, name: str, static_only: bool) -> int:
    from fwmanager.core.policy_check import ERROR
    from fwmanager.policytest import run_policy_test

    outcome = run_policy_test(client, name, load_catalog(), os.environ, static_only)
    print(_("Policy check for {p}:").format(p=_profile_label(name)))
    for f in outcome.findings:
        severity = _(SEVERITY_TEXT.get(f.severity, f.severity))
        print(f"  [{severity}] {describe_finding(f.code, f.detail)}")
        detail = finding_detail(f.code, f.detail)
        if detail:
            print(f"      {detail}")
    if not outcome.findings:
        print("  " + _("No problems found in the rules."))
    if any(f.severity == ERROR for f in outcome.findings):
        print(_("Live test skipped: fix the errors above first."))
    elif outcome.live is not None:
        live = outcome.live
        print("\n" + _("Live test (the profile is applied for a few seconds, then reverted):"))
        marks = {True: "ok", False: "FAIL", None: "n/a"}
        for before, after in zip(live.before, live.after, strict=False):
            print(f"  {describe_probe(before.name)}: {marks[before.ok]} -> {marks[after.ok]}")
        for probe in live.regressions:
            print("  " + _("BROKEN by this profile: {p}").format(p=describe_probe(probe)))
        if not live.restored:
            print(_("WARNING: previous rules could not be confirmed as restored."))
    print("\n" + _("Result: {v}").format(v=_(VERDICT_TEXT[outcome.verdict])))
    return {"safe": EXIT_OK, "warnings": EXIT_WARNINGS, "unsafe": EXIT_UNSAFE}[outcome.verdict]


def _print_status(client, as_json: bool) -> None:
    st = client.status()
    if as_json:
        print(json.dumps(st))
        return
    print(_("Firewall: {state}").format(state=_("on") if st["firewall_active"] else _("off")))
    active = st["active_profile"]
    if active:
        print(_("Profile in use: {p}").format(p=_profile_label(active)))
        titles = dict(client.list_services())
        print("  " + state.describe_profile(client.get_profile(active), titles))
    else:
        print(_("Profile in use: {p}").format(p=_("none")))
    if st["pending_profile"]:
        print(
            _("Waiting for confirmation of {p} ({n}s left)").format(
                p=_profile_label(st["pending_profile"]), n=st["seconds_left"]
            )
        )
        print("  " + _("Keep it: fwmanager-cli confirm    Undo it: fwmanager-cli rollback"))
    for name in st["blocking_conflicts"]:
        print(_("Warning: {name} is also managing the firewall.").format(name=name))


def _print_profiles(client) -> None:
    active = client.status()["active_profile"]
    titles = dict(client.list_services())
    names = client.list_profiles()
    ordered = [n for n in state.PROFILE_ORDER if n in names] + sorted(
        n for n in names if n not in state.PROFILE_ORDER
    )
    for name in ordered:
        mark = "*" if name == active else " "
        hint = state.profile_hint(name)
        print(f"{mark} {_profile_label(name)}" + (f": {hint}" if hint else ""))
        print("    " + state.describe_profile(client.get_profile(name), titles))
    print(_("* = in use. Switch with: fwmanager-cli apply NAME"))


def resolve_service(arg: str, titles: dict[str, str]) -> str:
    """A service by its id (ssh) or its title (Samba, "File sharing"), any case or accent."""
    wanted = _fold(arg)
    for sid, title in titles.items():
        if wanted in {_fold(sid), _fold(title), _fold(_(title))}:
            return sid
    for sid, title in titles.items():  # "samba" matches "File sharing (Samba)"
        if wanted and (wanted in _fold(title) or wanted in _fold(_(title))):
            return sid
    raise LookupError(arg)


def _edit_profile(client, args: argparse.Namespace) -> int:
    titles = dict(client.list_services())
    current = client.get_profile(args.profile)
    services = list(current["services"])
    try:
        allow = [resolve_service(a, titles) for a in args.allow]
        block = [resolve_service(a, titles) for a in args.block]
    except LookupError as exc:
        print(_("Unknown service: {name}").format(name=exc.args[0]), file=sys.stderr)
        print(_("You can use:"), file=sys.stderr)
        for sid, title in sorted(titles.items()):
            print(f"  {sid:<12} {_(title)}", file=sys.stderr)
        return EXIT_ERROR
    services += [s for s in allow if s not in services]
    services = [s for s in services if s not in block]
    ping = current["ping"] if args.ping is None else args.ping == "on"
    mdns = current["mdns"] if args.mdns is None else args.mdns == "on"
    kind, cidrs = current["source_kind"], list(current["cidrs"])
    if args.source:
        source = _fold(args.source)
        if source in ("anywhere", "any", _fold(_("anywhere"))):
            kind, cidrs = "any", []
        elif source in ("lan", "local", _fold(_("local"))):
            kind, cidrs = "lan", []
        else:
            problem = state.cidr_problem(args.source)
            if problem:
                print(problem, file=sys.stderr)
                return EXIT_ERROR
            kind, cidrs = "cidrs", state.parse_cidrs(args.source)
    client.set_profile(args.profile, services, ping, mdns, kind, cidrs)
    print(_("Saved. Now {p}:").format(p=_profile_label(args.profile)))
    print("  " + state.describe_profile(client.get_profile(args.profile), titles))
    if client.status()["active_profile"] == args.profile:
        print(
            _("It is in use: apply it again to use the changes: fwmanager-cli apply {p}").format(
                p=args.profile
            )
        )
    return EXIT_OK


def _print_networks(client, as_json: bool) -> None:
    nets = client.list_networks()
    if as_json:
        print(json.dumps([{"ssid": s, "uuid": u, "profile": p} for s, u, p in nets]))
        return
    if not nets:
        print(_("No networks remembered yet. Unknown Wi-Fi networks use Public."))
        return
    for ssid, uuid, profile in nets:
        name = ssid or _("Connection {id}").format(id=uuid[:8])
        print(f"{name}  →  {_profile_label(profile)}")


def run(args: argparse.Namespace, client: _Tracking | None = None) -> int:
    if args.cmd == "dispatch":
        from fwmanager.cli import dispatch

        return dispatch.run(args.iface, args.bus_address) if args.action == "up" else EXIT_OK

    client = client or _Tracking(Client(args.bus_address))
    if getattr(args, "profile", None):
        args.profile = resolve_profile(args.profile, client.list_profiles())
    if args.cmd == "test-policy":
        return _test_policy(client, args.profile, args.static)
    if args.cmd == "networks":
        if args.net_cmd == "list":
            _print_networks(client, args.json)
        elif args.net_cmd == "set":
            client.set_network_profile(args.ssid, args.uuid, args.profile)
            print(_("Saved."))
        else:
            client.remove_network(args.ssid, args.uuid)
            print(_("Forgotten."))
        return EXIT_OK
    if args.cmd == "apply":
        if args.yes:
            client.apply_profile(args.profile)
            client.confirm()
            print(_("Applied."))
            return EXIT_OK
        return _interactive_apply(client, args.profile)
    if args.cmd == "confirm":
        client.confirm()
        print(_("Confirmed."))
    elif args.cmd == "rollback":
        client.rollback()
        print(_("Previous rules restored."))
    elif args.cmd == "profiles":
        _print_profiles(client)
    elif args.cmd == "edit":
        return _edit_profile(client, args)
    elif args.cmd == "reset":
        client.reset_profile(args.profile)
        print(_("{p} is back to its defaults.").format(p=_profile_label(args.profile)))
    elif args.cmd == "status":
        _print_status(client, args.json)
    return EXIT_OK


def _cancelled(client: _Tracking | None) -> int:
    """Ctrl+C: a change this command started is undone now, not 30 seconds later."""
    print("", file=sys.stderr)
    if client is not None and client.started:
        try:
            client.rollback()
            print(_("Cancelled. Previous rules restored."), file=sys.stderr)
        except FwError:
            print(_("Cancelled."), file=sys.stderr)  # it was already undone or confirmed
    else:
        print(_("Cancelled."), file=sys.stderr)
    return EXIT_INTERRUPTED


def _setup_or_uninstall(args: argparse.Namespace) -> int:
    from fwmanager.cli import setup_cmd

    deps = setup_cmd.Deps(client=lambda: _Tracking(Client(args.bus_address)))
    try:
        if args.cmd == "setup":
            return setup_cmd.setup(deps, args.profile, args.yes)
        return setup_cmd.uninstall(deps, args.yes)
    except KeyboardInterrupt:
        print("\n" + _("Cancelled."), file=sys.stderr)
        return EXIT_INTERRUPTED
    except FwError as exc:
        print(_("Error: {msg}").format(msg=describe_error(exc)), file=sys.stderr)
        return EXIT_ERROR


def _rejoin_group(argv: list[str]) -> bool:
    """Just added to the group (by setup) but this shell predates it: run again with it."""
    from fwmanager.ui import relaunch, setup_state

    info = setup_state.group_info()
    if not (info.listed and not info.held):
        return False
    return relaunch.relaunch(relaunch.current_executable("fwmanager-cli"), args=argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    set_language(resolve_language(settings_path(), SYSTEM_CONFIG))
    argparse_text.install()
    argv = sys.argv[1:] if argv is None else argv
    parser = _dispatch_parser() if _is_dispatch(argv) else _parser()
    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return 2
    if args.cmd in ("setup", "uninstall"):
        return _setup_or_uninstall(args)
    client = None
    try:
        if args.cmd != "dispatch":
            client = _Tracking(Client(args.bus_address))
        return run(args, client)
    except KeyboardInterrupt:
        return _cancelled(client)
    except FwError as exc:
        if exc.code == "access_denied" and _rejoin_group(argv):
            return EXIT_ERROR  # only reached when the restart was not possible
        print(_("Error: {msg}").format(msg=describe_error(exc)), file=sys.stderr)
        hint = HINTS.get(exc.code)
        if hint:
            print(_(hint), file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
