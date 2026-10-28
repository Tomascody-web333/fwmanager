"""An in-memory stand-in for the daemon client, with the same calls and the same shapes."""

from fwmanager.core import rulebook
from fwmanager.core.profiles import Profile, Source
from fwmanager.core.services import load_builtin
from fwmanager.errors import FwError

SERVICES = [
    ("ssh", "SSH"),
    ("samba", "File sharing (Samba)"),
    ("web", "Web server (HTTP/HTTPS)"),
    ("ipp", "Printer sharing (IPP)"),
]


def default_profiles():
    def p(name, services, ping, mdns, kind):
        return {"name": name, "services": services, "ping": ping, "mdns": mdns,
                "source_kind": kind, "cidrs": [], "customized": False}  # fmt: skip

    return {
        "casa": p("casa", ["ssh", "samba", "ipp"], True, True, "lan"),
        "trabalho": p("trabalho", ["ssh"], True, False, "lan"),
        "publico": p("publico", [], False, False, "any"),
        "personalizado": p("personalizado", ["ssh"], False, False, "lan"),
    }


class FakeDaemon:
    def __init__(self):
        self.profiles = default_profiles()
        self.active = "publico"
        self.pending = ""
        self.seconds = 30
        self.ufw_installed = True
        self.firewall_active = True
        self.conflicts = []
        self.warnings = []
        self.startup = "ok"
        self.networks = [("Casa WiFi", "123e4567-e89b-12d3-a456-426614174000", "casa")]
        self.options = {"router_mode": False, "log_level": ""}
        self.events = [(1_790_000_000, "startup", "ok"), (1_790_000_050, "confirmed", "casa")]
        self.blocked = [("2026-10-27T17:32:01", "wlan0", "203.0.113.9", "192.168.1.5", "TCP", "22")]
        self.findings = []
        self.rules = []  # (id, template, params)
        self.calls = []
        self.error = None  # set to an FwError to make every call fail
        self.signal_cb = None


class FakeClient:
    def __init__(self, daemon: FakeDaemon | None = None):
        self.d = daemon or FakeDaemon()

    def _call(self, name, *args):
        self.d.calls.append((name, args))
        if self.d.error is not None:
            raise self.d.error

    def subscribe(self, cb):
        self.d.signal_cb = cb

    def status(self):
        self._call("status")
        d = self.d
        return {
            "ufw_installed": d.ufw_installed,
            "firewall_active": d.firewall_active,
            "active_profile": d.active,
            "pending_profile": d.pending,
            "seconds_left": d.seconds if d.pending else 0,
            "blocking_conflicts": list(d.conflicts),
            "warnings": list(d.warnings),
            "startup": d.startup,
        }

    def list_profiles(self):
        self._call("list_profiles")
        return sorted(self.d.profiles)

    def _objects(self):
        return {
            n: Profile(n, tuple(p["services"]), p["ping"], p["mdns"],
                       Source(p["source_kind"], tuple(p["cidrs"])))
            for n, p in self.d.profiles.items()
        }  # fmt: skip

    def _effective(self, extra=()):
        rules = [rulebook.Rule(i, t, dict(p)) for i, t, p in self.d.rules] + list(extra)
        return rulebook.compile_profiles(self._objects(), rules)

    def get_profile(self, name):
        self._call("get_profile", name)
        base, eff = dict(self.d.profiles[name]), self._effective()[name]
        base.update(services=list(eff.services), ping=eff.ping, mdns=eff.mdns,
                    source_kind=eff.source.kind, cidrs=list(eff.source.cidrs))  # fmt: skip
        base["affected_by_rules"] = not rulebook.same_effect(self._objects()[name], eff)
        return base

    def list_services(self):
        self._call("list_services")
        return list(SERVICES)

    def set_profile(self, name, services, ping, mdns, kind, cidrs=()):
        self._call("set_profile", name, list(services), ping, mdns, kind, list(cidrs))
        self.d.profiles[name] = {"name": name, "services": list(services), "ping": ping,
                                 "mdns": mdns, "source_kind": kind, "cidrs": list(cidrs),
                                 "customized": True}  # fmt: skip

    def reset_profile(self, name):
        self._call("reset_profile", name)
        self.d.profiles[name] = default_profiles()[name]

    def apply_profile(self, name):
        self._call("apply_profile", name)
        if self.d.pending:
            raise FwError("pending", code="apply_pending")
        self.d.pending = name

    def confirm(self):
        self._call("confirm")
        self.d.active, self.d.pending = self.d.pending, ""

    def rollback(self):
        self._call("rollback")
        self.d.pending = ""

    def check_profile(self, name):
        self._call("check_profile", name)
        return list(self.d.findings)

    def list_networks(self):
        self._call("list_networks")
        return list(self.d.networks)

    def set_network_profile(self, ssid, uuid, profile):
        self._call("set_network_profile", ssid, uuid, profile)
        self.d.networks = [n for n in self.d.networks if (n[0], n[1]) != (ssid, uuid)]
        self.d.networks.append((ssid, uuid, profile))

    def remove_network(self, ssid, uuid):
        self._call("remove_network", ssid, uuid)
        self.d.networks = [n for n in self.d.networks if (n[0], n[1]) != (ssid, uuid)]

    def get_options(self):
        self._call("get_options")
        return dict(self.d.options)

    def set_options(self, router_mode, log_level=""):
        self._call("set_options", router_mode, log_level)
        self.d.options = {"router_mode": router_mode, "log_level": log_level}

    def get_events(self):
        self._call("get_events")
        return list(self.d.events)

    def get_recent_blocked(self, limit=50):
        self._call("get_recent_blocked", limit)
        return list(self.d.blocked)

    def list_rules(self):
        self._call("list_rules")
        return [(i, t, dict(p)) for i, t, p in self.d.rules]

    def _validated(self, template, params):
        return rulebook.validate_params(
            template, params, load_builtin(), frozenset(self.d.profiles)
        )

    def preview_rule(self, template, params):
        self._call("preview_rule", template, params)
        clean = self._validated(template, params)
        before = self._effective()
        after = self._effective([rulebook.Rule("preview", template, clean)])

        def tri(v):
            return "" if v is None else ("on" if v else "off")

        return [
            (c.name, list(c.opened), list(c.closed), tri(c.ping), tri(c.mdns),
             c.source.kind if c.source else "", list(c.source.cidrs) if c.source else [])
            for c in rulebook.diff_profiles(before, after)
        ]  # fmt: skip

    def add_rule(self, template, params):
        self._call("add_rule", template, params)
        clean = self._validated(template, params)
        rid = rulebook.next_rule_id([rulebook.Rule(i, t, {}) for i, t, _ in self.d.rules])
        self.d.rules.append((rid, template, {k: v if isinstance(v, str) else list(v)
                                             for k, v in clean.items()}))  # fmt: skip
        return rid

    def remove_rule(self, rule_id):
        self._call("remove_rule", rule_id)
        self.d.rules = [r for r in self.d.rules if r[0] != rule_id]
