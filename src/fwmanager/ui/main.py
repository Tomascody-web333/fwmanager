"""``fwmanager`` entry point (runs as the normal user, never as root)."""

from __future__ import annotations

import os
import sys

from fwmanager.core.config import SYSTEM_CONFIG, resolve_language
from fwmanager.i18n import _, set_language

MIN_ADW = (1, 5)  # Adw.Dialog and the breakpoints need libadwaita 1.5 (Ubuntu 24.04, Fedora 40)


def runtime_problem(adw_version: tuple[int, int] | None) -> str | None:
    """Text explaining what is missing, or ``None`` if the toolkit is new enough."""
    if adw_version is None:
        return _(
            "fwmanager needs GTK 4 and libadwaita 1.5 or newer, and they were not found. "
            "On Ubuntu: sudo apt install gir1.2-gtk-4.0 gir1.2-adw-1"
        )
    if adw_version < MIN_ADW:
        return _(
            "fwmanager needs libadwaita 1.5 or newer, but this system has {found}. "
            "Use Ubuntu 24.04, Fedora 40 or later. The command line (fwmanager-cli) "
            "works on older systems."
        ).format(found=".".join(map(str, adw_version)))
    return None


def _adw_version() -> tuple[int, int] | None:
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
    except (ImportError, ValueError):
        return None
    return Adw.get_major_version(), Adw.get_minor_version()


def _rejoin_group(group_info=None, do_relaunch=None) -> None:
    """Set up earlier (e.g. `fwmanager-cli setup`), but this session predates the group: start
    again with it at once, instead of showing a "restart" step for something already done."""
    from fwmanager.ui import relaunch, setup_state

    info = (group_info or setup_state.group_info)()
    if info.listed and not info.held:
        (do_relaunch or relaunch.relaunch)()  # returns only when it cannot be done


def _announce_identity() -> None:
    """Before GTK starts: the name desktops use to tie this window to its launcher and icon.

    Started through ``python -c``, the program would otherwise call itself "-c", and X11
    desktops (XFCE, MATE, Cinnamon, KDE...) would show a generic icon in the task bar.
    """
    from gi.repository import GLib

    from fwmanager.ui import APP_ID

    GLib.set_prgname(APP_ID)
    GLib.set_application_name("fwmanager")


def _can_open_windows() -> bool:
    """False without a graphical session (over SSH, in a text console)."""
    from gi.repository import Gdk, Gtk

    # PyGObject's init_check can say yes with no display at all; the display is what counts.
    if not (Gtk.init_check() and Gdk.Display.get_default() is not None):
        return False
    from fwmanager.ui import APP_ID

    Gtk.Window.set_default_icon_name(APP_ID)  # title bars and task bars that show a window icon
    return True


def main(argv: list[str] | None = None) -> int:
    from fwmanager.ui.settings import settings_path

    set_language(resolve_language(settings_path(), SYSTEM_CONFIG))
    argv = list(sys.argv if argv is None else argv)
    background = "--background" in argv  # started at login: notifications, no window
    argv = [a for a in argv if a != "--background"]
    if os.geteuid() == 0:
        print(_("fwmanager must not run as root: the service does that part."), file=sys.stderr)
        return 1
    if background:
        from fwmanager.ui import relaunch

        _rejoin_group(do_relaunch=lambda: relaunch.relaunch(args=["--background"]))
    else:
        _rejoin_group()
    problem = runtime_problem(_adw_version())
    if problem:
        print(problem, file=sys.stderr)
        return 1
    _announce_identity()
    if not _can_open_windows():
        print(
            _(
                "fwmanager needs a graphical session to open its window. "
                "Over SSH or in a text console, use fwmanager-cli."
            ),
            file=sys.stderr,
        )
        return 1
    from fwmanager.ui.app import FwApplication

    return FwApplication(background=background).run(argv)


if __name__ == "__main__":
    sys.exit(main())
