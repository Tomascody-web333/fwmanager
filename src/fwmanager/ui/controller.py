"""Glue between the daemon and the windows, with no widgets in it.

Blocking D-Bus calls run in worker threads; results come back on the GTK main loop. The
client is created on the main thread so that daemon signals are delivered there too.
"""

from __future__ import annotations

import getpass
import os
from collections.abc import Callable
from dataclasses import dataclass, field

from gi.repository import GLib

from fwmanager.client import Client
from fwmanager.core.services import load_catalog
from fwmanager.errors import FwError
from fwmanager.policytest import run_policy_test
from fwmanager.ui import netinfo, state
from fwmanager.ui.worker import run_async

Done = Callable[[object, "FwError | None"], None]


@dataclass(frozen=True)
class Snapshot:
    screen: str
    status: dict | None = None
    problem: state.Problem | None = None
    error: FwError | None = None
    profiles_known: tuple[str, ...] = field(default=())


class Controller:
    def __init__(
        self,
        client_factory: Callable[[], Client] = Client,
        run: Callable = run_async,
        user: str | None = None,
    ) -> None:
        self._factory = client_factory
        self._run = run
        self._user = user or getpass.getuser()
        self._client: Client | None = None
        self._busy = False
        self._ticks = 0
        self._timer = 0
        self.applying = ""  # the profile being applied right now (ufw can take a few seconds)
        self.own_change = ""  # last profile applied from this window: its Confirmed is no news
        self.snapshot = Snapshot(state.DAEMON_DOWN)
        self.listeners: list[Callable[[Snapshot], None]] = []
        self.signal_listeners: list[Callable[[str, tuple], None]] = []
        self._connect()

    # -- connection ---

    def _connect(self) -> None:
        try:
            self._client = self._factory()
            self._client.subscribe(self._on_signal)
        except FwError:
            self._client = None

    def _on_signal(self, name: str, args: tuple) -> None:
        for fn in list(self.signal_listeners):
            fn(name, args)
        self.refresh()

    # -- status polling ---

    def refresh(self, done: Done | None = None) -> None:
        if self._client is None:
            self._connect()
        if self._client is None or self._busy:
            if self._client is None:
                self._publish(Snapshot(state.DAEMON_DOWN, None, self._problem(state.DAEMON_DOWN)))
            return
        self._busy = True
        client = self._client

        def finished(result, error) -> None:
            self._busy = False
            screen = state.classify(result, error)
            snap = Snapshot(screen, result, self._problem(screen, result), error)
            self._publish(snap)
            if done:
                done(result, error)

        self._run(client.status, finished)

    def _problem(self, screen: str, status: dict | None = None):
        return state.problem_for(screen, status, self._user)

    def _publish(self, snap: Snapshot) -> None:
        self.snapshot = snap
        for fn in list(self.listeners):
            fn(snap)

    def start_polling(self) -> None:
        """Every second while a change is pending, otherwise every third second."""
        if self._timer:
            return

        def tick() -> bool:
            self._ticks += 1
            pending = bool((self.snapshot.status or {}).get("pending_profile"))
            if pending or self._ticks % 3 == 0:
                self.refresh()
            return True

        self._timer = GLib.timeout_add_seconds(1, tick)
        self.refresh()

    def stop_polling(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    # -- operations ---

    def _op(self, fn: Callable[[Client], object], done: Done | None) -> None:
        def finished(result, error) -> None:
            if error is not None and error.code in ("access_denied", "daemon_unavailable"):
                self.refresh()  # the screen may have to change
            if done:
                done(result, error)

        if self._client is None:
            self._connect()
        client = self._client
        if client is None:
            finished(None, FwError("daemon is not running", code="daemon_unavailable"))
            return
        self._run(lambda: fn(client), finished)

    def _then_refresh(self, done: Done | None) -> Done:
        def callback(result, error) -> None:
            self.refresh()
            if done:
                done(result, error)

        return callback

    def apply(self, name: str, done: Done | None = None) -> None:
        self.applying = name
        self.own_change = name
        self._publish(self.snapshot)  # pages show "Applying…" at once

        def finished(result, error) -> None:
            self.applying = ""
            self._publish(self.snapshot)
            if done:
                done(result, error)

        self._op(lambda c: c.apply_profile(name), self._then_refresh(finished))

    def confirm(self, done: Done | None = None) -> None:
        self._op(lambda c: c.confirm(), self._then_refresh(done))

    def rollback(self, done: Done | None = None) -> None:
        self._op(lambda c: c.rollback(), self._then_refresh(done))

    def load_profiles(self, done: Done) -> None:
        """Result: ``(profiles, service_titles)`` with profiles in display order."""

        def fetch(c: Client):
            names = c.list_profiles()
            ordered = [n for n in state.PROFILE_ORDER if n in names] + sorted(
                n for n in names if n not in state.PROFILE_ORDER
            )
            return {n: c.get_profile(n) for n in ordered}, dict(c.list_services())

        self._op(fetch, done)

    def save_profile(self, name, services, ping, mdns, kind, cidrs, done: Done | None = None):
        self._op(lambda c: c.set_profile(name, services, ping, mdns, kind, cidrs), done)

    def reset_profile(self, name: str, done: Done | None = None) -> None:
        self._op(lambda c: c.reset_profile(name), done)

    def rules(self, done: Done) -> None:
        self._op(lambda c: c.list_rules(), done)

    def preview_rule(self, template: str, params: dict, done: Done) -> None:
        self._op(lambda c: c.preview_rule(template, params), done)

    def add_rule(self, template: str, params: dict, done: Done | None = None) -> None:
        self._op(lambda c: c.add_rule(template, params), done)

    def remove_rule(self, rule_id: str, done: Done | None = None) -> None:
        self._op(lambda c: c.remove_rule(rule_id), done)

    def networks(self, done: Done) -> None:
        self._op(lambda c: c.list_networks(), done)

    def set_network(self, ssid: str, uuid: str, profile: str, done: Done | None = None) -> None:
        self._op(lambda c: c.set_network_profile(ssid, uuid, profile), done)

    def remove_network(self, ssid: str, uuid: str, done: Done | None = None) -> None:
        self._op(lambda c: c.remove_network(ssid, uuid), done)

    def network_routers(self, done: Done) -> None:
        self._op(lambda c: c.list_network_routers(), done)

    def trust_router(self, ssid: str, uuid: str, done: Done | None = None) -> None:
        self._op(lambda c: c.trust_router(ssid, uuid), done)

    def export_settings(self, done: Done) -> None:
        self._op(lambda c: c.export_settings(), done)

    def import_settings(self, text: str, done: Done | None = None) -> None:
        self._op(lambda c: c.import_settings(text), done)

    def options(self, done: Done) -> None:
        self._op(lambda c: c.get_options(), done)

    def set_options(self, router_mode: bool, log_level: str, done: Done | None = None) -> None:
        self._op(lambda c: c.set_options(router_mode, log_level), done)

    def events(self, done: Done) -> None:
        self._op(lambda c: c.get_events(), done)

    def blocked(self, done: Done, limit: int = 100) -> None:
        self._op(lambda c: c.get_recent_blocked(limit), done)

    def allowed(self, done: Done, limit: int = 100) -> None:
        """Connections ufw let in (only recorded at logging level medium or high)."""
        self._op(lambda c: c.get_recent_allowed(limit), done)

    def current_network(self, done: Done, bus_address: str | None = None) -> None:
        self._run(lambda: netinfo.current_network(bus_address), done)

    def test_policy(self, name: str, static_only: bool, done: Done) -> None:
        """Result: a ``PolicyOutcome``. The live part applies the profile for a moment."""
        catalog = load_catalog()
        self._op(lambda c: run_policy_test(c, name, catalog, os.environ, static_only), done)
