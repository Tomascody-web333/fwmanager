"""Sentences and previews for plain-language rules (translated here, never in the daemon)."""

from __future__ import annotations

from fwmanager.core import rulebook as rb
from fwmanager.i18n import N_, _
from fwmanager.ui import state

TEMPLATE_TITLES = {
    rb.ALLOW_ONLY_IN: N_("Allow a service only in some profiles"),
    rb.BLOCK_ALL_EXCEPT: N_("Block everything coming in, except some services"),
    rb.RESTRICT_SOURCE: N_("Only accept connections from some networks"),
    rb.HIDE: N_("Hide this computer (no ping, no mDNS)"),
    rb.CLOSE_SERVICE: N_("Close a service in every profile"),
}


def template_title(template: str) -> str:
    return _(TEMPLATE_TITLES[template]) if template in TEMPLATE_TITLES else template


def _profiles(names) -> str:
    return ", ".join(state.profile_title(n) for n in names)


def _service(sid: str, titles: dict[str, str]) -> str:
    return _(titles.get(sid, sid))


def sentence(template: str, params: dict, titles: dict[str, str]) -> str:
    """The rule as one plain sentence, e.g. "Allow File sharing only in: Home"."""
    profiles = _profiles(params.get("profiles", ()))
    if template == rb.ALLOW_ONLY_IN:
        return _("Allow {service} only in: {profiles}").format(
            service=_service(params["service"], titles), profiles=profiles
        )
    if template == rb.BLOCK_ALL_EXCEPT:
        services = ", ".join(_service(s, titles) for s in params.get("services", ()))
        if not services:
            return _("Block everything coming in, in: {profiles}").format(profiles=profiles)
        return _("Block everything coming in, except {services}, in: {profiles}").format(
            services=services, profiles=profiles
        )
    if template == rb.RESTRICT_SOURCE:
        if params.get("scope") == "lan":
            return _("Only accept connections from the local network in: {profiles}").format(
                profiles=profiles
            )
        return _("Only accept connections from {networks} in: {profiles}").format(
            networks=", ".join(params.get("cidrs", ())), profiles=profiles
        )
    if template == rb.HIDE:
        return _("Hide this computer (no ping, no mDNS) in: {profiles}").format(profiles=profiles)
    if template == rb.CLOSE_SERVICE:
        return _("Close {service} in every profile").format(
            service=_service(params["service"], titles)
        )
    return template


def _source_part(kind: str, cidrs: list[str]) -> str:
    """A whole phrase per case: prepositions do not survive being glued onto a placeholder."""
    if kind == "lan":
        return _("only accepts connections from the local network")
    if kind == "cidrs":
        return _("only accepts connections from {networks}").format(networks=", ".join(cidrs))
    return _("accepts connections from anywhere")


def preview_lines(changes: list[tuple], titles: dict[str, str]) -> list[str]:
    """One line per profile that would change: what opens, what closes, ping, mDNS, who."""
    lines = []
    for name, opened, closed, ping, mdns, source_kind, cidrs in changes:
        parts = []
        if opened:
            parts.append(
                _("opens {services}").format(
                    services=", ".join(_service(s, titles) for s in opened)
                )
            )
        if closed:
            parts.append(
                _("closes {services}").format(
                    services=", ".join(_service(s, titles) for s in closed)
                )
            )
        if ping:
            parts.append(_("starts answering ping") if ping == "on" else _("stops answering ping"))
        if mdns:
            parts.append(_("turns mDNS on") if mdns == "on" else _("turns mDNS off"))
        if source_kind:
            parts.append(_source_part(source_kind, cidrs))
        lines.append(f"{state.profile_title(name)}: " + "; ".join(parts))
    return lines or [_("Nothing changes: the profiles already work like this.")]
