"""Translatable user-facing text for each error/status code."""

from __future__ import annotations

from fwmanager.errors import FwError
from fwmanager.i18n import N_, _


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
        "ufw_failed": _("ufw rejected the change."),
        "ufw_missing": _(
            "ufw is not installed. Install it first (for example: sudo apt install ufw)."
        ),
        "ufw_unsupported": _("ufw's configuration was edited in a way fwmanager cannot work with."),
        "apply_pending": _("Another change is waiting for confirmation."),
        "no_pending": _("There is no change waiting for confirmation."),
        "access_denied": _("You are not allowed to control the firewall."),
        "daemon_unavailable": _("The fwmanager service is not running."),
        "invalid_network": _("Invalid network (name or UUID)."),
        "too_many_networks": _("Too many saved networks."),
        "profile_too_big": _(
            "That would make the profile too large to apply quickly. "
            "Use fewer networks or fewer services."
        ),
        "firewall_conflict": _(
            "Another firewall manager (firewalld) is active. Disable it first, then try again."
        ),
        "state_corrupt": _("The saved firewall state was damaged; the safest profile was used."),
        "store_error": _("Could not save the settings."),
        "internal_error": _("Internal error."),
    }
    return table.get(code, table["internal_error"])


FINDINGS = {
    "foreign_statement": N_("The rules touch something fwmanager does not manage."),
    "ufw_unsupported": N_("ufw's configuration was edited in a way fwmanager cannot work with."),
    "ufw_missing": N_("ufw is not installed."),
    "ufw_rejects_rules": N_("ufw refused one of the rules."),
    "ufw_will_be_enabled": N_("ufw is off and will be turned on."),
    "ipv6_not_filtered": N_("IPv6 is not enabled in ufw, so it is not filtered."),
    "unparsed_rules": N_("Some fwmanager rules have an unexpected shape."),
    "will_add": N_("Rules that will be added."),
    "will_remove": N_("Rules that will be removed."),
    "router_mode": N_("Router mode may interfere with Docker, VMs and containers."),
    "missing_established": N_("Replies to your own connections would be blocked."),
    "missing_invalid_drop": N_("Invalid packets would not be dropped."),
    "missing_loopback": N_("Local (loopback) traffic would be blocked."),
    "missing_dhcp4": N_("Renewing the IPv4 address (DHCP) would fail."),
    "missing_dhcp6": N_("Renewing the IPv6 address (DHCPv6) would fail."),
    "missing_nd_neighbor_solicit": N_("IPv6 would stop working (neighbour discovery)."),
    "missing_nd_neighbor_advert": N_("IPv6 would stop working (neighbour discovery)."),
    "missing_nd_router_advert": N_("IPv6 would stop working (router advertisements)."),
    "missing_packet_too_big": N_("IPv6 connections could hang (packet-too-big)."),
    "missing_destination_unreachable": N_("Connections could hang (destination unreachable)."),
    "missing_time_exceeded": N_("Some diagnostics would fail (time exceeded)."),
    "exposed_to_any": N_("Allowed services are reachable from any network."),
    "ssh_ok": N_("Your SSH session and new SSH connections stay allowed."),
    "ssh_reconnect_blocked": N_("Your SSH session survives, but you could not open a new one."),
    "firewall_conflict": N_("Another firewall manager (firewalld) is active."),
    "docker_present": N_("Docker manages its own rules (they are left untouched)."),
    "libvirt_present": N_("libvirt manages its own rules (they are left untouched)."),
}
PROBES = {
    "gateway_v4": N_("Reach the IPv4 gateway"),
    "gateway_v6": N_("Reach the IPv6 gateway"),
    "dns": N_("DNS resolver answers"),
    "loopback_v4": N_("Local IPv4 connections"),
    "loopback_v6": N_("Local IPv6 connections"),
}


def describe_finding(code: str, fallback: str = "") -> str:
    """Translated text for a policy-check finding code (English detail as fallback)."""
    return _(FINDINGS[code]) if code in FINDINGS else fallback or code


# Findings whose detail is a list of rules ("10.0.0.0/8 → tcp 22"), the same in every language.
LISTED_DETAIL = ("will_add", "will_remove")


def finding_detail(code: str, detail: str) -> str:
    """The part of a finding worth showing under its sentence, or an empty string."""
    return detail if code in LISTED_DETAIL else ""


def describe_probe(name: str) -> str:
    return _(PROBES[name]) if name in PROBES else name


def describe_error(exc: FwError) -> str:
    return describe(exc.code)
