"""What the window should show, decided without any GTK. All user text goes through gettext."""

from __future__ import annotations

from dataclasses import dataclass

from fwmanager.errors import FwError
from fwmanager.i18n import N_, _

OK = "ok"
DAEMON_DOWN = "daemon_down"
NOT_IN_GROUP = "not_in_group"
UFW_MISSING = "ufw_missing"
CONFLICT = "conflict"

PROFILE_ORDER = ("casa", "trabalho", "publico", "personalizado")
PROFILE_TITLES = {
    "casa": N_("Home"),
    "trabalho": N_("Work"),
    "publico": N_("Public"),
    "personalizado": N_("Custom"),
}
PROFILE_HINTS = {
    "casa": N_("A network you trust, such as your own Wi-Fi."),
    "trabalho": N_("A shared network at work or school."),
    "publico": N_("The strictest: cafés, airports, hotels."),
    "personalizado": N_("Your own settings."),
}
SOURCE_TITLES = {
    "any": N_("Anywhere"),
    "lan": N_("Local network only"),
    "cidrs": N_("Specific networks"),
}
SOURCE_ORDER = ("any", "lan", "cidrs")


@dataclass(frozen=True)
class Problem:
    screen: str
    title: str
    body: str
    command: str | None  # what the user should run to fix it


def classify(status: dict | None, error: FwError | None) -> str:
    """Pick the screen from the daemon's answer (or the error that replaced it)."""
    if error is not None:
        if error.code == "access_denied":
            return NOT_IN_GROUP
        if error.code in ("daemon_unavailable", "internal_error"):
            return DAEMON_DOWN
        return DAEMON_DOWN
    if status is None:
        return DAEMON_DOWN
    if not status.get("ufw_installed", True):
        return UFW_MISSING
    if status.get("blocking_conflicts"):
        return CONFLICT
    return OK


def problem_for(screen: str, status: dict | None, user: str) -> Problem | None:
    if screen == DAEMON_DOWN:
        return Problem(
            screen,
            _("The firewall service is not running"),
            _("fwmanager needs its background service. Start it with this command:"),
            "sudo systemctl enable --now fwmanagerd",
        )
    if screen == NOT_IN_GROUP:
        return Problem(
            screen,
            _("Your account is not allowed to use fwmanager"),
            _(
                "Add your user to the fwmanager group, then log out and back in. "
                "No password is asked after that."
            ),
            f"sudo usermod -aG fwmanager {user}",
        )
    if screen == UFW_MISSING:
        return Problem(
            screen,
            _("ufw is not installed"),
            _("fwmanager is a friendly face for ufw, so ufw has to be installed first:"),
            "sudo apt install ufw",
        )
    if screen == CONFLICT:
        names = ", ".join((status or {}).get("blocking_conflicts", []))
        return Problem(
            screen,
            _("Another firewall manager is active"),
            _("{names} is managing the firewall. Turn it off first, then reopen fwmanager:").format(
                names=names
            ),
            f"sudo systemctl disable --now {names.split(', ')[0]}.service",
        )
    return None


def profile_title(name: str) -> str:
    return _(PROFILE_TITLES[name]) if name in PROFILE_TITLES else name


def profile_hint(name: str) -> str:
    return _(PROFILE_HINTS[name]) if name in PROFILE_HINTS else ""


def source_title(kind: str) -> str:
    return _(SOURCE_TITLES[kind]) if kind in SOURCE_TITLES else kind


def yes_no(value: bool) -> str:
    return _("yes") if value else _("no")


def describe_profile(details: dict, service_titles: dict[str, str]) -> str:
    """One line for a profile row: what is open, to whom, ping and mDNS."""
    services = [_(service_titles.get(s, s)) for s in details["services"]]
    opened = ", ".join(services) if services else _("nothing is open")
    return _("{opened} · from: {source} · ping: {ping} · mDNS: {mdns}").format(
        opened=opened,
        source=source_title(details["source_kind"]),
        ping=yes_no(details["ping"]),
        mdns=yes_no(details["mdns"]),
    )


def countdown_text(profile: str, seconds: int) -> str:
    return _("{profile} will be undone in {n} s unless you confirm.").format(
        profile=profile_title(profile), n=seconds
    )


def parse_cidrs(text: str) -> list[str]:
    """Split user input on commas, spaces and newlines."""
    return [p for p in text.replace(",", " ").split() if p]


def event_text(kind: str, detail: str) -> str:
    """Daemon event codes -> sentences (the daemon itself always logs in English)."""
    name = profile_title(detail) if detail else ""
    table = {
        "applied": _("Applied {name}, waiting for confirmation").format(name=name),
        "confirmed": _("Confirmed {name}").format(name=name),
        "reverted": _("Undid {name}").format(name=name),
        "profile_edited": _("Edited {name}").format(name=name),
        "options": _("Changed the settings"),
        "rule_added": _("Added a rule"),
        "rule_removed": _("Removed a rule"),
        "denied": _("Refused a request from an account that is not allowed"),
        "startup": _("Service started"),
    }
    return table.get(kind, kind)
