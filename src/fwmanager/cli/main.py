"""fwmanager-cli: command-line client for the fwmanager daemon."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from fwmanager.client import Client
from fwmanager.core.profiles import DEFAULT_PROFILES
from fwmanager.core.rules import RenderOptions, render
from fwmanager.core.services import load_catalog
from fwmanager.errors import FwError, ValidationError
from fwmanager.i18n import _, set_language
from fwmanager.messages import describe_error

EXIT_OK, EXIT_ERROR, EXIT_REVERTED = 0, 1, 2


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fwmanager-cli", description=_("Firewall manager client"))
    p.add_argument("--bus-address", help=argparse.SUPPRESS)  # development/tests only
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("apply", help=_("apply a profile (auto-reverts unless confirmed)"))
    sp.add_argument("profile", help=_("profile name"))
    sp.add_argument("--yes", action="store_true", help=_("confirm immediately, no countdown"))

    sp = sub.add_parser("render", help=_("print the rules for a profile without applying"))
    sp.add_argument("profile", help=_("profile name"))

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


def run(args: argparse.Namespace) -> int:
    if args.cmd == "render":
        profile = DEFAULT_PROFILES.get(args.profile)
        if profile is None:
            raise ValidationError("unknown profile", code="unknown_profile")
        print(render(profile, load_catalog(), RenderOptions()), end="")
        return EXIT_OK

    if args.cmd == "dispatch":
        from fwmanager.cli import dispatch

        return dispatch.run(args.iface, args.bus_address) if args.action == "up" else EXIT_OK

    client = Client(args.bus_address)
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
