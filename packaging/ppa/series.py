"""Which Ubuntu releases the PPA builds for, from Launchpad's list of Ubuntu series.

Reads the JSON of https://api.launchpad.net/devel/ubuntu/series on stdin and prints
"name version" for every release still looked after (or being prepared) from 24.04 on: older
ones lack Python 3.11 and libadwaita 1.5.
"""

from __future__ import annotations

import json
import sys

WANTED = {"Current Stable Release", "Supported", "Active Development", "Pre-release Freeze"}
OLDEST = (24, 4)


def pick(data: dict) -> list[tuple[str, str]]:
    chosen = []
    for series in data.get("entries", []):
        try:
            number = tuple(int(part) for part in str(series["version"]).split("."))
        except (KeyError, ValueError):
            continue
        if series.get("status") in WANTED and number >= OLDEST:
            chosen.append((number, series["name"], series["version"]))
    return [(name, version) for _number, name, version in sorted(chosen)]


def main() -> int:
    for name, version in pick(json.load(sys.stdin)):
        print(name, version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
