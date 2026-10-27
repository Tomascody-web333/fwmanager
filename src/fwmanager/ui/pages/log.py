"""Log: what the service did, and the most recent blocked connection attempts."""

from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

from fwmanager.i18n import _  # noqa: E402
from fwmanager.ui import state  # noqa: E402
from fwmanager.ui.common import button, clear, toast_error  # noqa: E402


class LogPage(Adw.PreferencesPage):
    def __init__(self, controller) -> None:
        super().__init__(title=_("Log"), icon_name="view-list-symbolic")
        self._c = controller
        self._event_rows: list[Adw.ActionRow] = []
        self._block_rows: list[Adw.ActionRow] = []

        top = Adw.PreferencesGroup()
        self.refresh_btn = button(_("Refresh"), icon="view-refresh-symbolic")
        self.refresh_btn.connect("clicked", lambda *_: self.load())
        top.set_header_suffix(self.refresh_btn)
        self.add(top)

        self.events = Adw.PreferencesGroup(title=_("What fwmanager did"))
        self.blocked = Adw.PreferencesGroup(
            title=_("Blocked connection attempts"),
            description=_("The most recent ones, newest first. Turn logging on in Settings."),
        )
        self.add(self.events)
        self.add(self.blocked)

    def load(self) -> None:
        self._c.events(self._events_loaded)
        self._c.blocked(self._blocked_loaded)

    def _events_loaded(self, result, error) -> None:
        if error is not None:
            toast_error(self, error)
            return
        clear(self.events, self._event_rows)
        for stamp, kind, detail in reversed(result[-50:]):
            row = Adw.ActionRow(
                title=state.event_text(kind, detail),
                subtitle=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stamp)),
            )
            self.events.add(row)
            self._event_rows.append(row)
        if not self._event_rows:
            row = Adw.ActionRow(title=_("Nothing yet."))
            self.events.add(row)
            self._event_rows.append(row)

    def _blocked_loaded(self, result, error) -> None:
        if error is not None:
            toast_error(self, error)
            return
        clear(self.blocked, self._block_rows)
        for when, iface, src, dst, proto, dport in result:
            target = f"{dst}:{dport}" if dport else dst
            row = Adw.ActionRow(
                title=_("{src} tried to reach {target}").format(src=src, target=target),
                subtitle=" · ".join(p for p in (proto, iface, when) if p),
            )
            self.blocked.add(row)
            self._block_rows.append(row)
        if not self._block_rows:
            row = Adw.ActionRow(title=_("No blocked attempts found."))
            self.blocked.add(row)
            self._block_rows.append(row)
