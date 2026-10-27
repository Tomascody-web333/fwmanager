"""Small GTK helpers shared by the pages."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from fwmanager.errors import FwError  # noqa: E402
from fwmanager.i18n import _  # noqa: E402
from fwmanager.messages import describe_error  # noqa: E402


def _show_text_as_typed() -> None:
    """libadwaita reads row, toast, group and status-page text as Pango markup by default.

    Much of what we show was typed by someone else: a Wi-Fi name comes from any access point in
    range. As markup, "Café & Bar" renders as an empty line and ``<span size='90000'>`` restyles
    the window. Every page imports this module before building a widget, so switching markup
    off here covers them all, including ones written later.
    """
    from gi.repository import GLib

    def plain_by_default(cls):
        init = cls.__init__

        def __init__(self, *args, **kwargs):
            kwargs.setdefault("use_markup", False)
            init(self, *args, **kwargs)

        cls.__init__ = __init__

    def escaped(cls, props):  # these have no switch: their text is always markup
        init = cls.__init__

        def __init__(self, *args, **kwargs):
            for prop in props:
                if isinstance(kwargs.get(prop), str):
                    kwargs[prop] = GLib.markup_escape_text(kwargs[prop])
            init(self, *args, **kwargs)

        cls.__init__ = __init__
        for prop in props:
            setter = getattr(cls, f"set_{prop}")
            setattr(cls, f"set_{prop}", lambda self, text, _set=setter: _set(
                self, GLib.markup_escape_text(text)
            ))  # fmt: skip

    plain_by_default(Adw.PreferencesRow)  # every row: action, switch, combo, entry, expander
    plain_by_default(Adw.Toast)
    escaped(Adw.PreferencesGroup, ("title", "description"))
    escaped(Adw.StatusPage, ("description",))


_show_text_as_typed()

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
        overlay.add_toast(Adw.Toast(title=text))


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


def warning_row() -> Adw.ActionRow:
    """A row for a warning sentence, hidden until it has something to say."""
    row = Adw.ActionRow(title_lines=0, visible=False)
    row.add_prefix(Gtk.Image.new_from_icon_name("dialog-warning-symbolic"))
    row.add_css_class("warning")
    return row


def show_warning(row: Adw.ActionRow, text: str) -> None:
    row.set_title(text)
    row.set_visible(bool(text))


def clear(group: Adw.PreferencesGroup, rows: list) -> None:
    for row in rows:
        group.remove(row)
    rows.clear()
