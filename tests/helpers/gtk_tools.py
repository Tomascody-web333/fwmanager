"""Inspect GTK widget trees in tests (Gtk is imported lazily: it needs a display first)."""


def texts(widget):
    """Every label/title/subtitle visible in a widget tree, for 'is this on screen?' checks."""
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    found = []

    def walk(w):
        if w is None:
            return
        if isinstance(w, Gtk.Label) and w.get_visible():
            found.append(w.get_text())
        if isinstance(w, Gtk.Button) and w.get_label():
            found.append(w.get_label())
        if isinstance(w, Adw.ButtonContent) and w.get_label():
            found.append(w.get_label())
        for prop in ("title", "subtitle"):
            if w.find_property(prop) is not None and w.get_visible():
                value = w.get_property(prop)
                if isinstance(value, str) and value:
                    found.append(value)
        child = w.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(widget)
    return found


def find(widget, predicate):
    """All descendants (and the widget itself) for which ``predicate`` is true."""
    out = []

    def walk(w):
        if predicate(w):
            out.append(w)
        child = w.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(widget)
    return out
