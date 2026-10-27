"""Small GTK helpers shared by the pages."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from fwmanager.errors import FwError  # noqa: E402
from fwmanager.i18n import _  # noqa: E402
from fwmanager.messages import describe_error  # noqa: E402

SEVERITY_ICON = {
    "error": "dialog-error-symbolic",
    "warning": "dialog-warning-symbolic",
    "info": "emblem-ok-symbolic",
}


def label_for_screen_readers(widget: Gtk.Widget, text: str) -> None:
    """Icon-only controls need a spoken name."""
    widget.update_property([Gtk.AccessibleProperty.LABEL], [text])


def toast(widget: Gtk.Widget, text: str) -> None:
    """Show a toast on the nearest overlay; harmless if there is none."""
    root = widget.get_root()
    overlay = getattr(root, "toasts", None)
    if overlay is not None:
        overlay.add_toast(Adw.Toast.new(text))


def toast_error(widget: Gtk.Widget, error: FwError) -> None:
    toast(widget, _("Something went wrong: {why}").format(why=describe_error(error)))


def button(label: str, *, suggested: bool = False, flat: bool = False, icon: str | None = None):
    """A button: text, icon, or both side by side (never silently dropping the text)."""
    btn = Gtk.Button(valign=Gtk.Align.CENTER)
    if icon and label:
        btn.set_child(Adw.ButtonContent(label=label, icon_name=icon))
    elif icon:
        btn.set_icon_name(icon)
    else:
        btn.set_label(label)
    if suggested:
        btn.add_css_class("suggested-action")
    if flat:
        btn.add_css_class("flat")
    return btn


def clear(group: Adw.PreferencesGroup, rows: list) -> None:
    for row in rows:
        group.remove(row)
    rows.clear()
