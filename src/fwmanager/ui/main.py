"""``fwmanager`` entry point (runs as the normal user, never as root)."""

from __future__ import annotations

import os
import sys


def main(argv: list[str] | None = None) -> int:
    if os.geteuid() == 0:
        print(
            "fwmanager must not run as root; the service does the privileged work.", file=sys.stderr
        )
        return 1
    from fwmanager.ui.app import FwApplication

    return FwApplication().run(sys.argv if argv is None else argv)


if __name__ == "__main__":
    sys.exit(main())
