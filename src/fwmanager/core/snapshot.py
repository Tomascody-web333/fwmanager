"""Snapshot and exact restore of the ``inet fwmanager`` table."""

from __future__ import annotations

from dataclasses import dataclass

from fwmanager.core.nft import Nft
from fwmanager.core.rules import FAMILY, TABLE

# Objects must be created in dependency order: sets/counters before chains before rules.
_ORDER = {
    "table": 0,
    "set": 1,
    "map": 1,
    "counter": 1,
    "quota": 1,
    "limit": 1,
    "chain": 2,
    "rule": 3,
}


@dataclass(frozen=True)
class Snapshot:
    """``objects`` is the raw ``nft -j list table`` output, ``None`` if the table was absent."""

    objects: tuple[dict, ...] | None

    @property
    def exists(self) -> bool:
        return self.objects is not None


def take(nft: Nft) -> Snapshot:
    objs = nft.list_table_json(FAMILY, TABLE)
    return Snapshot(None if objs is None else tuple(objs))


def _strip_handle(obj: dict) -> dict:
    return {k: v for k, v in obj.items() if k != "handle"}


def restore(nft: Nft, snap: Snapshot) -> None:
    """Replace the current table with the snapshot in a single transaction."""
    table_ref = {"family": FAMILY, "name": TABLE}
    cmds: list[dict] = [{"add": {"table": dict(table_ref)}}, {"delete": {"table": dict(table_ref)}}]
    if snap.objects is not None:
        items = []
        for obj in snap.objects:
            ((kind, body),) = obj.items()
            if kind in _ORDER:
                items.append((_ORDER[kind], kind, _strip_handle(body)))
        items.sort(key=lambda t: t[0])  # stable: rule order is preserved
        cmds += [{"add": {kind: body}} for _, kind, body in items]
    nft.apply_json(cmds)


def normalize(objects: tuple[dict, ...] | list[dict] | None) -> list[dict] | None:
    """Drop handles and metainfo so two listings can be compared."""
    if objects is None:
        return None
    out = []
    for obj in objects:
        ((kind, body),) = obj.items()
        if kind != "metainfo":
            out.append({kind: _strip_handle(body)})
    return out
