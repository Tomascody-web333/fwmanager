"""Render the app icon (SVG) to the PNG sizes desktops look for, at install time.

Most desktops read the SVG, but some menus, docks and older environments only take PNGs.
No new dependency: GdkPixbuf comes with GTK, which the window needs anyway.
"""

from __future__ import annotations

import sys
from pathlib import Path

SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512)


def render(svg: Path, hicolor: Path, name: str, sizes=SIZES) -> list[Path]:
    """Write ``hicolor/NxN/apps/<name>.png`` for every size; returns the files written."""
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    written = []
    for size in sizes:
        out = hicolor / f"{size}x{size}" / "apps" / f"{name}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(str(svg), size, size)
        pixbuf.savev(str(out), "png", [], [])
        written.append(out)
    return written


def main(argv: list[str] | None = None) -> int:
    """``icon_build SVG HICOLOR_DIR NAME``. Without an SVG renderer it warns and goes on."""
    svg, hicolor, name = (argv if argv is not None else sys.argv[1:])[:3]
    try:
        render(Path(svg), Path(hicolor), name)
    except Exception as exc:  # no GdkPixbuf or no SVG loader: the SVG alone still works
        print(f"icon_build: PNG icons skipped ({exc})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
