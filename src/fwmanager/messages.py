"""Translatable user-facing text for each error/status code."""

from __future__ import annotations

from fwmanager.errors import FwError
from fwmanager.i18n import _


def describe(code: str) -> str:
    table = {
        "invalid_input": _("Invalid input."),
        "invalid_profile": _("Invalid profile."),
        "invalid_service": _("Invalid service."),
        "invalid_port": _("Invalid port."),
        "invalid_protocol": _("Invalid protocol."),
        "invalid_cidr": _("Invalid network address (CIDR)."),
        "invalid_source": _("Invalid traffic source."),
        "invalid_option": _("Invalid option."),
        "unknown_service": _("Unknown service."),
        "unknown_profile": _("Unknown profile."),
        "nft_failed": _("The firewall rejected the rules."),
        "nft_unavailable": _("The nft program could not be run."),
        "apply_pending": _("Another change is waiting for confirmation."),
        "no_pending": _("There is no change waiting for confirmation."),
        "access_denied": _("You are not allowed to control the firewall."),
        "daemon_unavailable": _("The fwmanager service is not running."),
        "invalid_network": _("Invalid network (name or UUID)."),
        "too_many_networks": _("Too many saved networks."),
        "firewall_conflict": _(
            "Another firewall (ufw or firewalld) is active. Disable it first, then try again."
        ),
        "state_corrupt": _("The saved firewall state was damaged; the safest profile was used."),
        "store_error": _("Could not save the settings."),
        "internal_error": _("Internal error."),
    }
    return table.get(code, table["internal_error"])


def describe_error(exc: FwError) -> str:
    return describe(exc.code)
