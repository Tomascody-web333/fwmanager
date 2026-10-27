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


def main(argv: list[str] | None = None) -> int:
    from fwmanager.ui.settings import settings_path

    set_language(resolve_language(settings_path(), SYSTEM_CONFIG))
    if os.geteuid() == 0:
        print(_("fwmanager must not run as root: the service does that part."), file=sys.stderr)
        return 1
    problem = runtime_problem(_adw_version())
    if problem:
        print(problem, file=sys.stderr)
        return 1
    from fwmanager.ui.app import FwApplication

    return FwApplication().run(sys.argv if argv is None else argv)


if __name__ == "__main__":
    sys.exit(main())
