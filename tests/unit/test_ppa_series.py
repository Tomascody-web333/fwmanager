"""Which Ubuntu releases the PPA builds for (packaging/ppa/series.py)."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("series", ROOT / "packaging/ppa/series.py")
series = importlib.util.module_from_spec(spec)
spec.loader.exec_module(series)


def entry(name, version, status):
    return {"name": name, "version": version, "status": status}


def test_supported_releases_from_24_04_on_in_order():
    data = {"entries": [
        entry("questing", "25.10", "Supported"),
        entry("jammy", "22.04", "Supported"),          # too old: no libadwaita 1.5
        entry("noble", "24.04", "Supported"),
        entry("plucky", "25.04", "Obsolete"),          # no longer built
        entry("resolute", "26.04", "Current Stable Release"),
        entry("next", "26.10", "Active Development"),
        entry("future", "27.04", "Future"),            # not open yet
    ]}  # fmt: skip
    assert series.pick(data) == [
        ("noble", "24.04"),
        ("questing", "25.10"),
        ("resolute", "26.04"),
        ("next", "26.10"),
    ]


def test_odd_entries_are_skipped():
    data = {"entries": [{"name": "x"}, entry("y", "not.a.number", "Supported"), {}]}
    assert series.pick(data) == []
    assert series.pick({}) == []
