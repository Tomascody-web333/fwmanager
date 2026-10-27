"""fwmanagerd entry point."""

from __future__ import annotations

import argparse
import logging
import signal
import sys

from gi.repository import Gio, GLib

from fwmanager.core.manager import CONFIRM_TIMEOUT, FirewallManager
from fwmanager.core.nft import Nft
from fwmanager.core.profiles import DEFAULT_PROFILES
from fwmanager.core.scheduler import GLibScheduler
from fwmanager.core.services import load_catalog
from fwmanager.daemon import auth
from fwmanager.daemon.interface import BUS_NAME
from fwmanager.daemon.service import ControlService

log = logging.getLogger("fwmanager")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fwmanagerd")
    ap.add_argument("--bus-address", help="private bus (development/tests); default: system bus")
    ap.add_argument("--group", default=auth.GROUP, help="group allowed to use the daemon")
    ap.add_argument(
        "--confirm-timeout",
        type=int,
        default=CONFIRM_TIMEOUT,
        choices=range(1, 301),
        metavar="SECONDS",
        help="seconds to confirm a change before it is reverted",
    )
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)

    if args.bus_address:
        conn = Gio.DBusConnection.new_for_address_sync(
            args.bus_address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None,
            None,
        )
    else:
        conn = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

    manager = FirewallManager(Nft(), load_catalog(), GLibScheduler())
    service = ControlService(
        manager,
        DEFAULT_PROFILES,
        lambda uid: auth.is_member(uid, args.group),
        args.confirm_timeout,
    )
    service.register(conn)

    loop = GLib.MainLoop()
    code = {"value": 0}

    def name_lost(*_a) -> None:
        log.error("could not own %s", BUS_NAME)
        code["value"] = 1
        loop.quit()

    Gio.bus_own_name_on_connection(
        conn, BUS_NAME, Gio.BusNameOwnerFlags.DO_NOT_QUEUE, None, name_lost
    )

    def stop() -> bool:
        log.info("shutting down")
        loop.quit()
        return False

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, stop)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, stop)
    log.info("fwmanagerd started")
    loop.run()
    service.shutdown()
    return code["value"]


if __name__ == "__main__":
    sys.exit(main())
