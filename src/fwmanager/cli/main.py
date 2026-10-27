"""fwmanager-cli: command-line client.

Phase 1 runs the core in-process (needs root). From phase 2 on, the same
commands will talk to ``fwmanagerd`` over D-Bus.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from fwmanager.core.manager import CONFIRM_TIMEOUT, FirewallManager
from fwmanager.core.nft import Nft
from fwmanager.core.profiles import DEFAULT_PROFILES, Profile
from fwmanager.core.rules import RenderOptions, render
from fwmanager.core.scheduler import GLibScheduler
from fwmanager.core.services import load_catalog
from fwmanager.errors import FwError
from fwmanager.i18n import _, set_language
from fwmanager.messages import describe_error

EXIT_OK, EXIT_ERROR, EXIT_REVERTED = 0, 1, 2


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fwmanager-cli", description=_("Firewall manager client"))
    sub = p.add_subparsers(dest="cmd", required=True)

    def profile_arg(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("profile", help=_("profile name"))
        sp.add_argument("--router", action="store_true", help=_("enable router mode"))
        sp.add_argument("--log", action="store_true", help=_("log blocked packets"))

    sp = sub.add_parser("apply", help=_("apply a profile (auto-reverts unless confirmed)"))
    profile_arg(sp)
    sp.add_argument("--yes", action="store_true", help=_("confirm immediately, no countdown"))
    sp.add_argument("--timeout", type=int, default=CONFIRM_TIMEOUT, help=_("seconds to confirm"))

    sp = sub.add_parser("render", help=_("print the rules for a profile without applying"))
    profile_arg(sp)

    sp = sub.add_parser("status", help=_("show the firewall state"))
    sp.add_argument("--json", action="store_true")

    sub.add_parser("confirm", help=_("confirm the pending change"))
    sub.add_parser("rollback", help=_("revert the pending change"))
    return p


def _find_profile(name: str) -> Profile:
    try:
        return DEFAULT_PROFILES[name]
    except KeyError:
        from fwmanager.errors import ValidationError

        raise ValidationError("unknown profile", code="unknown_profile") from None


def _interactive_apply(mgr: FirewallManager, args, options: RenderOptions) -> int:
    from gi.repository import GLib

    loop = GLib.MainLoop()
    result = {"code": EXIT_OK}

    def on_revert(_name: str) -> None:
        result["code"] = EXIT_REVERTED
        loop.quit()

    mgr.on_revert = on_revert
    mgr.apply(_find_profile(args.profile), options, timeout=args.timeout)

    def on_stdin(fd, _cond) -> bool:
        line = os.read(fd, 256).decode(errors="replace").strip().lower()
        if line in ("r", "n"):
            mgr.rollback()
        else:
            mgr.confirm()
            print(_("Confirmed."))
            loop.quit()
        return False

    def tick() -> bool:
        if mgr.pending_profile() is None:
            return False
        left = mgr.seconds_left()
        print(
            "\r" + _("Press Enter to confirm, r to revert ({n}s left)... ").format(n=left),
            end="",
            flush=True,
        )
        return True

    GLib.io_add_watch(sys.stdin.fileno(), GLib.IO_IN, on_stdin)
    GLib.timeout_add_seconds(1, tick)
    tick()
    loop.run()
    if result["code"] == EXIT_REVERTED:
        print("\n" + _("Previous rules restored."))
    return result["code"]


def run(args: argparse.Namespace, nft: Nft) -> int:
    catalog = load_catalog()
    options = RenderOptions(
        router_mode=getattr(args, "router", False), log_blocked=getattr(args, "log", False)
    )
    if args.cmd == "render":
        print(render(_find_profile(args.profile), catalog, options), end="")
        return EXIT_OK
    mgr = FirewallManager(nft, catalog, GLibScheduler())
    if args.cmd == "status":
        st = mgr.status()
        if args.json:
            print(json.dumps(st))
        else:
            print(_("Active profile: {p}").format(p=st["active_profile"] or _("none")))
        return EXIT_OK
    if args.cmd == "apply":
        if args.yes:
            mgr.apply(_find_profile(args.profile), options, require_confirm=False)
            print(_("Applied."))
            return EXIT_OK
        return _interactive_apply(mgr, args, options)
    # confirm / rollback act on state held by a running process.
    print(_("This command needs the fwmanagerd daemon (not available yet)."), file=sys.stderr)
    return EXIT_ERROR


def main(argv: list[str] | None = None, nft: Nft | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    set_language(os.environ.get("LANG"))
    args = _parser().parse_args(argv)
    try:
        return run(args, nft or Nft())
    except FwError as exc:
        print(_("Error: {msg}").format(msg=describe_error(exc)), file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
