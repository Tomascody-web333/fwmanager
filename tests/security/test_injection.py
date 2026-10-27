"""Malicious input must be rejected before it can reach nftables."""

import itertools
import re

import pytest

from fwmanager.core.profiles import DEFAULT_PROFILES, Profile, Source
from fwmanager.core.rules import RenderOptions, render
from fwmanager.core.services import load_builtin, parse_catalog
from fwmanager.errors import ValidationError

EVIL = [
    'x"; flush ruleset; "',
    "x\nflush ruleset",
    "x\r\nadd table ip evil",
    "x}\n}\ntable ip evil {",
    "x;drop",
    "$(reboot)",
    "`id`",
    "x\x00y",
    "../../etc/passwd",
    "a b",
    "{ 1, 2 }",
]


@pytest.mark.parametrize("evil", EVIL)
def test_evil_profile_and_service_names(evil):
    with pytest.raises(ValidationError):
        Profile(evil)
    with pytest.raises(ValidationError):
        Profile("ok", services=(evil,))


@pytest.mark.parametrize("evil", EVIL + ["0.0.0.0/0; accept", "1.1.1.1\n2.2.2.2"])
def test_evil_cidrs(evil):
    with pytest.raises(ValidationError):
        Source("cidrs", (evil,))


@pytest.mark.parametrize("evil", EVIL)
def test_evil_catalog_entries(evil):
    with pytest.raises(ValidationError):
        parse_catalog({evil: {"ports": ["22/tcp"]}})
    with pytest.raises(ValidationError):
        parse_catalog({"ok": {"ports": [evil]}})


@pytest.mark.parametrize("evil", EVIL)
def test_evil_log_rate(evil):
    with pytest.raises(ValidationError):
        RenderOptions(log_rate=evil)


@pytest.mark.parametrize(
    "bad_port", ["0/tcp", "65536/tcp", "-1/tcp", "22/tcp,23/tcp", "22", "a/tcp"]
)
def test_invalid_ports_in_catalog(bad_port):
    with pytest.raises(ValidationError):
        parse_catalog({"ok": {"ports": [bad_port]}})


# Every rendered line must consist of tokens we know how to produce.
SAFE_LINE = re.compile(r'^[A-Za-z0-9_ ,:./{};"\t-]*$')


def test_rendered_scripts_contain_only_safe_characters():
    catalog = load_builtin()
    profiles = list(DEFAULT_PROFILES.values()) + [
        Profile("all", services=tuple(catalog), ping=True, mdns=True),
        Profile("c", services=("ssh",), source=Source("cidrs", ("10.0.0.0/8", "fd00::/8"))),
    ]
    for p, router, log in itertools.product(profiles, (False, True), (False, True)):
        script = render(p, catalog, RenderOptions(router_mode=router, log_blocked=log))
        for line in script.splitlines():
            assert SAFE_LINE.match(line), line
        assert "flush" not in script
        assert script.count("table inet fwmanager") == 3  # add, delete, definition
        assert "ip evil" not in script


@pytest.mark.parametrize("title", ["a\nb", "a\x00b", "a\x1b[31m", "x" * 81, ""])
def test_evil_catalog_titles(title):
    with pytest.raises(ValidationError):
        parse_catalog({"ok": {"title": title, "ports": ["22/tcp"]}})
