"""Strict validators. Nothing from a client reaches ufw without passing here."""

from __future__ import annotations

import ipaddress
import re

from fwmanager.errors import ValidationError

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
PORT_SPEC_RE = re.compile(r"^(\d{1,5})(?:-(\d{1,5}))?/(tcp|udp)$")
CIDR_CHARS_RE = re.compile(r"^[0-9a-fA-F:.]+(?:/\d{1,3})?$")

PROTOCOLS = ("tcp", "udp")
MAX_CIDRS = 64


def validate_name(value: object, what: str = "name") -> str:
    """Profile and service identifiers: lowercase, digits, ``_`` and ``-`` only."""
    if not isinstance(value, str) or not NAME_RE.fullmatch(value):
        raise ValidationError(f"invalid {what}", code=f"invalid_{what}")
    return value


def validate_port(value: object) -> int:
    # bool is an int subclass; reject it explicitly.
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ValidationError("invalid port", code="invalid_port")
    return value


def validate_protocol(value: object) -> str:
    if value not in PROTOCOLS:
        raise ValidationError("invalid protocol", code="invalid_protocol")
    return value  # type: ignore[return-value]


def parse_port_spec(value: object) -> tuple[int, int, str]:
    """Parse ``"22/tcp"`` or ``"1714-1764/udp"`` into ``(first, last, proto)``."""
    if not isinstance(value, str):
        raise ValidationError("invalid port spec", code="invalid_port")
    m = PORT_SPEC_RE.fullmatch(value)
    if not m:
        raise ValidationError("invalid port spec", code="invalid_port")
    first = validate_port(int(m.group(1)))
    last = validate_port(int(m.group(2))) if m.group(2) else first
    if last < first:
        raise ValidationError("port range is reversed", code="invalid_port")
    return first, last, m.group(3)


def validate_cidr(value: object) -> str:
    """Return the canonical network string; a bare address becomes a /32 or /128."""
    if not isinstance(value, str) or not CIDR_CHARS_RE.fullmatch(value):
        raise ValidationError("invalid CIDR", code="invalid_cidr")
    try:
        net = ipaddress.ip_network(value, strict=True)
    except ValueError as exc:
        raise ValidationError("invalid CIDR", code="invalid_cidr") from exc
    return str(net)


def validate_cidrs(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)) or not values or len(values) > MAX_CIDRS:
        raise ValidationError("invalid CIDR list", code="invalid_cidr")
    return tuple(dict.fromkeys(validate_cidr(v) for v in values))
