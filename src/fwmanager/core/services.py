"""Service catalog: validated mapping of service id -> ports."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from fwmanager.core.validate import parse_port_spec, validate_name
from fwmanager.errors import ValidationError

MAX_FILE_BYTES = 64 * 1024
MAX_PORT_ENTRIES = 32
MAX_TITLE_LEN = 80


@dataclass(frozen=True)
class PortRange:
    first: int
    last: int
    proto: str


@dataclass(frozen=True)
class Service:
    id: str
    title: str
    ports: tuple[PortRange, ...]


def _parse_entry(sid: object, raw: object) -> Service:
    service_id = validate_name(sid, "service")
    if not isinstance(raw, dict) or set(raw) - {"title", "ports"}:
        raise ValidationError("malformed service entry", code="invalid_service")
    title = raw.get("title", service_id)
    if not isinstance(title, str) or not title.isprintable() or not title.strip():
        raise ValidationError("invalid service title", code="invalid_service")
    if len(title) > MAX_TITLE_LEN:
        raise ValidationError("invalid service title", code="invalid_service")
    specs = raw.get("ports")
    if not isinstance(specs, list) or not 0 < len(specs) <= MAX_PORT_ENTRIES:
        raise ValidationError("invalid service ports", code="invalid_service")
    ports = tuple(PortRange(*parse_port_spec(s)) for s in specs)
    return Service(service_id, title, ports)


def parse_catalog(data: object) -> dict[str, Service]:
    if not isinstance(data, dict):
        raise ValidationError("malformed catalog", code="invalid_service")
    return {sid: _parse_entry(sid, raw) for sid, raw in data.items()}


def _load_file(path: Path) -> dict[str, Service]:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValidationError("catalog file too large", code="invalid_service")
    try:
        with path.open("rb") as fh:
            return parse_catalog(tomllib.load(fh))
    except tomllib.TOMLDecodeError as exc:
        raise ValidationError("catalog is not valid TOML", code="invalid_service") from exc


def load_builtin() -> dict[str, Service]:
    text = resources.files("fwmanager").joinpath("data/services.toml").read_bytes()
    return parse_catalog(tomllib.loads(text.decode("utf-8")))


def load_catalog(extra_files: list[Path] | None = None) -> dict[str, Service]:
    """Built-in catalog plus validated extension files.

    Extensions may add services but never redefine an existing id.
    """
    catalog = load_builtin()
    for path in extra_files or []:
        for sid, service in _load_file(Path(path)).items():
            if sid in catalog:
                raise ValidationError("duplicate service id", code="invalid_service")
            catalog[sid] = service
    return catalog
