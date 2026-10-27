"""Integration tests against a real nft inside a private network namespace."""

import json
import subprocess
import sys
import textwrap

import pytest
from gi.repository import GLib

from fwmanager.core import snapshot as snap
from fwmanager.core.manager import FirewallManager
from fwmanager.core.profiles import DEFAULT_PROFILES
from fwmanager.core.rules import RenderOptions
from fwmanager.core.scheduler import GLibScheduler
from fwmanager.core.services import load_builtin
from fwmanager.errors import NftError, PendingError

pytestmark = pytest.mark.integration

CAT = load_builtin()


class CountingScheduler(GLibScheduler):
    def __init__(self):
        super().__init__()
        self.scheduled = 0

    def call_later(self, seconds, callback):
        self.scheduled += 1
        return super().call_later(seconds, callback)


@pytest.fixture
def mgr(nft):
    return FirewallManager(nft, CAT, CountingScheduler())


def table(nft):
    return snap.normalize(nft.list_table_json("inet", "fwmanager"))


def pump(seconds):
    """Run the default GLib main context for ``seconds`` (the timers live there)."""
    ctx = GLib.MainContext.default()
    end = GLib.get_monotonic_time() + int(seconds * 1_000_000)
    while GLib.get_monotonic_time() < end:
        ctx.iteration(False)
        GLib.usleep(10_000)


def test_apply_creates_table_with_policy_drop(mgr, nft):
    mgr.apply(DEFAULT_PROFILES["publico"], require_confirm=False)
    objs = nft.list_table_json("inet", "fwmanager")
    chains = {o["chain"]["name"]: o["chain"] for o in objs if "chain" in o}
    assert chains["input"]["policy"] == "drop"
    assert chains["forward"]["policy"] == "accept"
    assert chains["output"]["policy"] == "accept"
    assert mgr.status()["active_profile"] == "publico"


def test_reapply_replaces_without_duplicates(mgr, nft):
    mgr.apply(DEFAULT_PROFILES["casa"], require_confirm=False)
    first = table(nft)
    mgr.apply(DEFAULT_PROFILES["casa"], require_confirm=False)
    assert table(nft) == first


def test_publico_keeps_ipv6_essentials(mgr, nft):
    mgr.apply(DEFAULT_PROFILES["publico"], require_confirm=False)
    text = json.dumps(nft.list_table_json("inet", "fwmanager"))
    for needle in (
        "nd-neighbor-solicit",
        "nd-router-advert",
        "nd-neighbor-advert",
        "packet-too-big",
    ):
        assert needle in text
    assert "echo-request" not in text


def test_loopback_works_with_drop_policy(mgr, nft, netns_prefix):
    mgr.apply(DEFAULT_PROFILES["publico"], require_confirm=False)
    script = textwrap.dedent(
        """
        import fcntl, socket, struct
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        ifr = struct.pack("16sH14x", b"lo", 0x1 | 0x40)  # IFF_UP | IFF_RUNNING
        fcntl.ioctl(s, 0x8914, ifr)  # SIOCSIFFLAGS
        families = [(socket.AF_INET, "127.0.0.1")]
        try:
            socket.socket(socket.AF_INET6).close()
            families.append((socket.AF_INET6, "::1"))
        except OSError:
            pass  # kernel booted with ipv6.disable=1
        for fam, addr in families:
            srv = socket.socket(fam); srv.bind((addr, 0)); srv.listen(1)
            cli = socket.socket(fam); cli.settimeout(3)
            cli.connect((addr, srv.getsockname()[1]))
        print("ok", len(families))
        """
    )
    out = subprocess.run(
        [*netns_prefix, sys.executable, "-c", script], capture_output=True, text=True, timeout=20
    )
    assert out.stdout.startswith("ok"), out.stderr


def test_other_tables_untouched(mgr, nft):
    nft.run(["add", "table", "ip", "docker_like"])
    nft.run(["add", "chain", "ip", "docker_like", "c", "{ type filter hook forward priority 0; }"])
    nft.run(["add", "rule", "ip", "docker_like", "c", "counter", "accept"])
    nft.run(["add", "table", "inet", "tailscale_like"])
    before = nft.run(["-j", "list", "ruleset"])

    def others():
        data = json.loads(nft.run(["-j", "list", "ruleset"]))["nftables"]
        return [
            o
            for o in data
            if "metainfo" not in o
            and o.get("table", {}).get("name") != "fwmanager"
            and o.get("chain", {}).get("table") != "fwmanager"
            and o.get("rule", {}).get("table") != "fwmanager"
        ]

    expected = others()
    mgr.apply(DEFAULT_PROFILES["publico"], require_confirm=True, timeout=30)
    assert others() == expected
    mgr.rollback()
    assert others() == expected
    assert before  # sanity: ruleset listing worked


def test_confirm_cancels_timer(mgr, nft):
    mgr.apply(DEFAULT_PROFILES["publico"], timeout=1)
    assert mgr.pending_profile() == "publico"
    mgr.confirm()
    pump(2.5)
    assert mgr.status()["active_profile"] == "publico"
    assert mgr.pending_profile() is None


def test_timeout_restores_exact_previous_table(mgr, nft):
    mgr.apply(DEFAULT_PROFILES["casa"], require_confirm=False)
    before = table(nft)
    mgr.apply(DEFAULT_PROFILES["publico"], timeout=1)
    assert table(nft) != before
    pump(2.5)
    assert mgr.pending_profile() is None
    assert table(nft) == before
    assert mgr.status()["active_profile"] == "casa"


def test_timeout_removes_table_if_it_did_not_exist(mgr, nft):
    assert nft.list_table_json("inet", "fwmanager") is None
    reverted = []
    mgr.on_revert = reverted.append
    mgr.apply(DEFAULT_PROFILES["publico"], timeout=1)
    pump(2.5)
    assert nft.list_table_json("inet", "fwmanager") is None
    assert reverted == ["publico"]


def test_manual_rollback_restores_snapshot(mgr, nft):
    mgr.apply(DEFAULT_PROFILES["trabalho"], require_confirm=False)
    before = table(nft)
    mgr.apply(DEFAULT_PROFILES["casa"], RenderOptions(router_mode=True, log_blocked=True))
    mgr.rollback()
    assert table(nft) == before


def test_second_apply_rejected_and_single_timer(mgr, nft):
    sched = mgr._sched
    mgr.apply(DEFAULT_PROFILES["casa"], timeout=30)
    snapshot_after_first = table(nft)
    with pytest.raises(PendingError) as exc:
        mgr.apply(DEFAULT_PROFILES["publico"], timeout=30)
    assert exc.value.code == "apply_pending"
    assert sched.scheduled == 1
    assert table(nft) == snapshot_after_first  # rejected request changed nothing
    mgr.rollback()


def test_confirm_or_rollback_without_pending(mgr):
    for fn in (mgr.confirm, mgr.rollback):
        with pytest.raises(PendingError) as exc:
            fn()
        assert exc.value.code == "no_pending"


def test_failed_apply_leaves_ruleset_and_no_timer(mgr, nft, monkeypatch):
    mgr.apply(DEFAULT_PROFILES["casa"], require_confirm=False)
    before = table(nft)
    from fwmanager.core import manager as manager_mod

    monkeypatch.setattr(
        manager_mod, "render", lambda *a, **k: "add table inet fwmanager\nthis is not nft\n"
    )
    with pytest.raises(NftError):
        mgr.apply(DEFAULT_PROFILES["publico"])
    assert mgr.pending_profile() is None
    assert table(nft) == before


def test_cli_apply_yes_and_status(nft, capsys):
    from fwmanager.cli.main import main

    assert main(["apply", "publico", "--yes"], nft=nft) == 0
    assert main(["status", "--json"], nft=nft) == 0
    out = capsys.readouterr().out.splitlines()
    assert json.loads(out[-1])["active_profile"] == "publico"


def test_cli_unknown_profile(nft, capsys):
    from fwmanager.cli.main import main

    assert main(["apply", "nope", "--yes"], nft=nft) == 1
    assert nft.list_table_json("inet", "fwmanager") is None
