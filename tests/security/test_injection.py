"""Malicious input must be rejected before it can reach ufw."""

import re

import pytest

from fwmanager.core.profiles import DEFAULT_PROFILES, Profile, Source
from fwmanager.core.services import load_builtin, parse_catalog
from fwmanager.core.ufw_rules import translate
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


@pytest.mark.parametrize(
    "bad_port", ["0/tcp", "65536/tcp", "-1/tcp", "22/tcp,23/tcp", "22", "a/tcp"]
)
def test_invalid_ports_in_catalog(bad_port):
    with pytest.raises(ValidationError):
        parse_catalog({"ok": {"ports": [bad_port]}})


# Every argument handed to ufw must consist of tokens we know how to produce.
SAFE_ARG = re.compile(r"^[A-Za-z0-9_.:,/-]+$")


def test_ufw_arguments_contain_only_safe_characters():
    catalog = load_builtin()
    profiles = list(DEFAULT_PROFILES.values()) + [
        Profile("all", services=tuple(catalog), ping=True, mdns=True),
        Profile("c", services=("ssh",), source=Source("cidrs", ("10.0.0.0/8", "fd00::/8"))),
    ]
    for p in profiles:
        for rule in translate(p, catalog):
            for argv in (rule.add_argv(p.name), rule.delete_argv()):
                for arg in argv:
                    assert SAFE_ARG.match(arg), arg
                assert argv[0] in ("allow", "delete")
                assert not {"reload", "reset", "disable", "--force"} & set(argv)


@pytest.mark.parametrize("title", ["a\nb", "a\x00b", "a\x1b[31m", "x" * 81, ""])
def test_evil_catalog_titles(title):
    with pytest.raises(ValidationError):
        parse_catalog({"ok": {"title": title, "ports": ["22/tcp"]}})
