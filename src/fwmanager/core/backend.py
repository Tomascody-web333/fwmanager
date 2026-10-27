"""The ufw backend: apply a profile, snapshot and restore ufw's configuration exactly."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from fwmanager.core import ufw_files
from fwmanager.core.profiles import Profile
from fwmanager.core.services import Service
from fwmanager.core.ufw_rules import Listing, Rule, parse_added, translate
from fwmanager.core.ufw_runner import Ufw, UfwError, UfwMissing

log = logging.getLogger("fwmanager")


@dataclass(frozen=True)
class Options:
    """Daemon-wide settings, never taken from a client."""

    router_mode: bool = False  # filter forwarded traffic (ufw: default deny routed)
    log_level: str | None = None  # None: leave ufw's logging alone; else off|low|medium|high

    def __post_init__(self) -> None:
        if self.log_level not in (None, "off", "low", "medium", "high"):
            from fwmanager.errors import ValidationError

            raise ValidationError("invalid log level", code="invalid_option")


@dataclass(frozen=True)
class Plan:
    add: list[Rule]
    remove: list[Rule]
    keep: list[Rule]
    skipped_v6: bool


Snapshot = ufw_files.FilesSnapshot


class UfwBackend:
    def __init__(self, ufw: Ufw, catalog: dict[str, Service]) -> None:
        self.ufw = ufw
        self.catalog = catalog

    # -- reading ---------------------------------------------------------------

    def listing(self) -> Listing:
        return parse_added(self.ufw.run(["show", "added"]))

    def plan(self, profile: Profile) -> Plan:
        ipv6 = self.ufw.ipv6_enabled()
        desired = translate(profile, self.catalog, ipv6)
        cur = self.listing()
        have_ours = set(cur.managed)
        want = set(desired)
        # A rule the user already has identically needs no copy of ours (and must survive).
        add = [r for r in desired if r not in have_ours and r not in cur.foreign]
        remove = [r for r in cur.managed if r not in want]
        keep = [r for r in desired if r in have_ours]
        skipped = profile.source.kind in ("lan", "cidrs") and not ipv6
        return Plan(add, remove, keep, skipped)

    def active_profile(self) -> str | None:
        return ufw_files.active_marker(self.ufw.config_dir)

    def is_active(self) -> bool:
        return self.ufw.is_active()

    # -- snapshot --------------------------------------------------------------

    def snapshot(self) -> Snapshot:
        root = self.ufw.rootdir or Path("/")
        return Snapshot(ufw_files.take_files(root), self.ufw.is_active())

    def restore(self, snap: Snapshot) -> None:
        """Put every ufw file back byte for byte, then make the kernel match."""
        root = self.ufw.rootdir or Path("/")
        ufw_files.restore_files(root, snap.files)
        if snap.was_active:
            if self.ufw.is_active():
                self.ufw.run(["reload"])
            else:
                self.ufw.run(["--force", "enable"])
        elif self.ufw.is_active():
            self.ufw.run(["--force", "disable"])
            # `disable` rewrites ufw.conf; put the snapshot's copy back once more.
            ufw_files.restore_files(root, {"etc/ufw/ufw.conf": snap.files["etc/ufw/ufw.conf"]})

    # -- applying --------------------------------------------------------------

    def apply(self, profile: Profile, options: Options | None = None) -> Plan:
        """Make ufw match ``profile``. Removing comes before adding so a switch can only
        briefly close services, never briefly open extra ones."""
        options = options or Options()
        plan = self.plan(profile)
        v6 = self.ufw.ipv6_enabled()
        # `ufw default` and `ufw reload` re-load the whole ruleset (~2 s each): only when needed.
        needs_reload = ufw_files.apply_block(
            self.ufw.config_dir, profile.name, profile.ping, profile.mdns, v6
        )
        for direction, wanted in self._wanted_defaults(options).items():
            if self._current_defaults().get(direction) != wanted:
                self.ufw.run(["default", wanted, direction])
        if options.log_level:
            self.ufw.run(["logging", options.log_level])
        for rule in plan.remove:
            self.ufw.run(rule.delete_argv())
        for rule in plan.add:
            self.ufw.run(rule.add_argv(profile.name))
        # Relabel kept rules when they used to belong to another profile.
        self._relabel(profile, plan)
        if not self.ufw.is_active():
            self.ufw.run(["--force", "enable"])
        elif needs_reload:
            self.ufw.run(["reload"])  # picks up the before.rules block
        return plan

    @staticmethod
    def _wanted_defaults(options: Options) -> dict[str, str]:
        return {
            "incoming": "deny",
            "outgoing": "allow",
            "routed": "deny" if options.router_mode else "allow",
        }

    def _current_defaults(self) -> dict[str, str]:
        """Read /etc/default/ufw: DEFAULT_INPUT/OUTPUT/FORWARD_POLICY as ufw's words."""
        try:
            text = self.ufw.default_file.read_text()
        except OSError:
            return {}
        names = {"INPUT": "incoming", "OUTPUT": "outgoing", "FORWARD": "routed"}
        words = {"DROP": "deny", "ACCEPT": "allow", "REJECT": "reject"}
        out = {}
        for m in re.finditer(r'^DEFAULT_(INPUT|OUTPUT|FORWARD)_POLICY="(\w+)"', text, re.M):
            out[names[m.group(1)]] = words.get(m.group(2), m.group(2).lower())
        return out

    def _relabel(self, profile: Profile, plan: Plan) -> None:
        cur = self.listing().managed
        for rule in plan.keep:
            if cur.get(rule) not in (None, profile.name):
                self.ufw.run(rule.delete_argv())
                self.ufw.run(rule.add_argv(profile.name))

    def remove_everything(self) -> None:
        """Uninstall helper: drop every rule we own and the managed block."""
        try:
            for rule in self.listing().managed:
                self.ufw.run(rule.delete_argv())
            ufw_files.remove_blocks(self.ufw.config_dir)
            if self.ufw.is_active():
                self.ufw.run(["reload"])
        except (UfwError, UfwMissing):
            log.exception("could not fully clean ufw")
            raise
