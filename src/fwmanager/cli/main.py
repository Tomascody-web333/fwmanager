"""fwmanager-cli: command-line client for the fwmanager daemon."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from fwmanager.client import Client
from fwmanager.core.services import load_catalog
from fwmanager.errors import FwError
from fwmanager.i18n import N_, _, set_language
from fwmanager.messages import describe_error, describe_finding, describe_probe

EXIT_OK, EXIT_ERROR, EXIT_REVERTED = 0, 1, 2


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fwmanager-cli", description=_("Firewall manager client"))
    p.add_argument("--bus-address", help=argparse.SUPPRESS)  # development/tests only
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("apply", help=_("apply a profile (auto-reverts unless confirmed)"))
    sp.add_argument("profile", help=_("profile name"))
    sp.add_argument("--yes", action="store_true", help=_("confirm immediately, no countdown"))

    sp = sub.add_parser("status", help=_("show the firewall state"))
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("dispatch", help=argparse.SUPPRESS)  # called by NetworkManager
    sp.add_argument("iface")
    sp.add_argument("action")

    sp = sub.add_parser("networks", help=_("manage the network to profile mapping"))
    nsub = sp.add_subparsers(dest="net_cmd", required=True)
    nsub.add_parser("list")
    for name in ("set", "remove"):
        nsp = nsub.add_parser(name)
        nsp.add_argument("--ssid", default="")
        nsp.add_argument("--uuid", default="")
        if name == "set":
            nsp.add_argument("profile")

    sp = sub.add_parser("test-policy", help=_("check that a profile will not break the machine"))
    sp.add_argument("profile", help=_("profile name"))
    sp.add_argument(
        "--static", action="store_true", help=_("only analyse the rules, change nothing")
    )

    sub.add_parser("confirm", help=_("confirm the pending change"))
    sub.add_parser("rollback", help=_("revert the pending change"))
    sub.add_parser("profiles", help=_("list available profiles"))
    return p


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


def _test_policy(client: Client, name: str, static_only: bool) -> int:
    from fwmanager.core.policy_check import ERROR
    from fwmanager.policytest import run_policy_test

    outcome = run_policy_test(client, name, load_catalog(), os.environ, static_only)
    print(_("Policy check for {p}:").format(p=name))
    for f in outcome.findings:
        print(f"  [{f.severity}] {describe_finding(f.code, f.detail)}")
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


def run(args: argparse.Namespace) -> int:
    if args.cmd == "dispatch":
        from fwmanager.cli import dispatch

        return dispatch.run(args.iface, args.bus_address) if args.action == "up" else EXIT_OK

    client = Client(args.bus_address)
    if args.cmd == "test-policy":
        return _test_policy(client, args.profile, args.static)
    if args.cmd == "networks":
        if args.net_cmd == "list":
            for ssid, uuid, profile in client.list_networks():
                print(f"{profile}\t{ssid}\t{uuid}")
        elif args.net_cmd == "set":
            client.set_network_profile(args.ssid, args.uuid, args.profile)
        else:
            client.remove_network(args.ssid, args.uuid)
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
        print("\n".join(client.list_profiles()))
    elif args.cmd == "status":
        st = client.status()
        if args.json:
            print(json.dumps(st))
        else:
            print(_("Active profile: {p}").format(p=st["active_profile"] or _("none")))
            if st["pending_profile"]:
                print(
                    _("Waiting for confirmation of {p} ({n}s left)").format(
                        p=st["pending_profile"], n=st["seconds_left"]
                    )
                )
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    set_language(os.environ.get("LANG"))
    args = _parser().parse_args(argv)
    try:
        return run(args)
    except FwError as exc:
        print(_("Error: {msg}").format(msg=describe_error(exc)), file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
