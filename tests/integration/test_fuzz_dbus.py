"""Hostile input against every method of the real daemon (private bus, private ufw tree).

Every call must end either in success or in one of the daemon's own validation errors:
never an internal error, a crash, or a change to the firewall from a method that is not
supposed to make one. Then the saved state must still load on the next start.
"""

import random

import pytest
from gi.repository import Gio, GLib
from helpers.harness import Daemon

pytestmark = pytest.mark.integration

HOSTILE = [
    "",
    " ",
    "casa",
    "casa ",
    " casa",
    "CASA",
    "casa\n",
    "casa\x01",
    "ca\tsa",
    "-casa",
    "--force",
    "casa; ufw disable",
    "$(reboot)",
    "`id`",
    "../../etc/passwd",
    "/etc/shadow",
    "a" * 33,
    "x" * 70000,
    "ssh",
    "ssh\nallow 1:65535/tcp",
    "22/tcp",
    "0.0.0.0/0",
    "::/0",
    "10.0.0.0/8",
    "192.168.1.1/33",
    "1.2.3.4/-1",
    "١٢٣",
    "ｃａｓａ",
    "caſa",
    "casa​",
    "‮casa",
    "é",
    "😀",
    "\udcff".encode("utf-8", "surrogatepass").decode("utf-8", "replace"),
    "lan",
    "any",
    "cidrs",
    "off",
    "low",
    "full",
    "high ",
    "{}",
    "%s%n%x",
    "\\",
    "'",
    '"',
    "allow_only_in",
    "block_all_except",
    "restrict_source",
    "hide",
    "close_service",
    "r1",
    "r0",
    "r-1",
    "r999999999999",
    "00000000-0000-0000-0000-000000000000",
    "8a1e9c3b-1d2e-4f5a-9b6c-7d8e9f0a1b2c",
    "8A1E9C3B-1D2E-4F5A-9B6C-7D8E9F0A1B2C",
]


def rnd_str(r):
    return (
        r.choice(HOSTILE)
        if r.random() < 0.8
        else "".join(chr(r.randint(1, 0x2FFF)) for _ in range(r.randint(0, 40)))
    )


def rnd_strs(r):
    return [rnd_str(r) for _ in range(r.choice([0, 1, 2, 5, 70, 300]))]


def rnd_variant(r, depth=0):
    kind = r.choice(("s as b i u v d a" if depth < 2 else "s as b i").split())
    if kind == "s":
        return GLib.Variant("s", rnd_str(r))
    if kind == "as":
        return GLib.Variant("as", rnd_strs(r))
    if kind == "b":
        return GLib.Variant("b", r.random() < 0.5)
    if kind == "i":
        return GLib.Variant("i", r.randint(-(2**31), 2**31 - 1))
    if kind == "u":
        return GLib.Variant("u", r.randint(0, 2**32 - 1))
    if kind == "d":
        return GLib.Variant("d", r.choice([float("nan"), float("inf"), -0.0, 1e308]))
    if kind == "v":
        return GLib.Variant("v", rnd_variant(r, depth + 1))
    return GLib.Variant("a{sv}", {rnd_str(r): rnd_variant(r, depth + 1) for _ in range(3)})


PARAM_KEYS = ["service", "services", "profiles", "scope", "cidrs", "except", "x", ""]


def rnd_params(r):
    out = {}
    for _ in range(r.randint(0, 6)):
        key = r.choice(PARAM_KEYS) if r.random() < 0.8 else rnd_str(r)
        out[key] = rnd_variant(r)
    return out


CALLS = {
    "CheckProfile": lambda r: ("(s)", (rnd_str(r),)),
    "GetProfile": lambda r: ("(s)", (rnd_str(r),)),
    "SetProfile": lambda r: (
        "(sasbbsas)",
        (rnd_str(r), rnd_strs(r), r.random() < 0.5, r.random() < 0.5, rnd_str(r), rnd_strs(r)),
    ),
    "ResetProfile": lambda r: ("(s)", (rnd_str(r),)),
    "SetOptions": lambda r: ("(bs)", (r.random() < 0.5, rnd_str(r))),
    "GetRecentBlocked": lambda r: ("(u)", (r.choice([0, 1, 50, 2**31, 2**32 - 1]),)),
    "PreviewRule": lambda r: ("(sa{sv})", (rnd_str(r), rnd_params(r))),
    "AddRule": lambda r: ("(sa{sv})", (rnd_str(r), rnd_params(r))),
    "RemoveRule": lambda r: ("(s)", (rnd_str(r),)),
    "SetNetworkProfile": lambda r: ("(sss)", (rnd_str(r), rnd_str(r), rnd_str(r))),
    "RemoveNetwork": lambda r: ("(ss)", (rnd_str(r), rnd_str(r))),
    "GetNetworkProfile": lambda r: ("(ss)", (rnd_str(r), rnd_str(r))),
    "ApplyProfile": lambda r: ("(s)", (r.choice([h for h in HOSTILE if h != "casa"]),)),
}


def call(conn, method, sig, args):
    return conn.call_sync(
        "org.fwmanager.Control", "/org/fwmanager/Control", "org.fwmanager.Control",
        method, GLib.Variant(sig, args), None, Gio.DBusCallFlags.NONE, 20000, None,
    )  # fmt: skip


@pytest.mark.parametrize("seed", range(3))
def test_hostile_calls_never_break_the_daemon(
    nft, netns_prefix, private_bus, tmp_path, ufw_root, seed
):
    state = tmp_path / "state"
    d = Daemon(netns_prefix, private_bus, ufw_root, timeout=3, state_dir=state)
    r = random.Random(seed)
    conn = d.client._conn
    unexpected = []
    try:
        for _ in range(250):
            method = r.choice(list(CALLS))
            sig, args = CALLS[method](r)
            try:
                call(conn, method, sig, args)
            except GLib.Error as exc:
                name = Gio.DBusError.get_remote_error(exc) or ""
                if not name.startswith("org.fwmanager.Error.") or name.endswith("InternalError"):
                    unexpected.append((method, repr(args)[:200], name, exc.message[:200]))
            assert d.proc.poll() is None, f"daemon died on {method}{repr(args)[:300]}"
        assert unexpected == [], unexpected[:10]
        assert d.fp() == d.initial  # nothing above may touch the firewall
        assert d.client.status()["pending_profile"] == ""
        profiles = d.client.list_profiles()
        for p in profiles:
            d.client.get_profile(p)
    finally:
        log = d.stop()
    assert "Traceback" not in log, log[-3000:]
    # Whatever got saved must load again, and the daemon must still answer.
    d2 = Daemon(netns_prefix, private_bus, ufw_root, timeout=3, state_dir=state)
    try:
        assert set(d2.client.list_profiles()) == set(profiles)
        d2.client.list_rules()
        d2.client.list_networks()
    finally:
        log2 = d2.stop()
    assert "Traceback" not in log2, log2[-3000:]
