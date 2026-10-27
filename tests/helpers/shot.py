"""Render a realized GTK widget to a PNG (works under Xvfb), and pump the main loop."""

from gi.repository import GLib


def pump(seconds: float = 0.3) -> None:
    ctx = GLib.MainContext.default()
    end = GLib.get_monotonic_time() + int(seconds * 1_000_000)
    while GLib.get_monotonic_time() < end:
        while ctx.iteration(False):
            pass
        GLib.usleep(5000)


def screenshot(widget, path: str) -> None:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    pump(0.4)
    width, height = widget.get_width(), widget.get_height()
    paintable = Gtk.WidgetPaintable.new(widget)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, width, height)
    node = snapshot.to_node()
    renderer = widget.get_native().get_renderer()
    texture = renderer.render_texture(node, None)
    texture.save_to_png(path)
