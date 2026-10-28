#!/usr/bin/env python3
"""Refresh po/*.pot and po/*.po from the sources (keeps existing translations).

python3 scripts/update-po.py          # rewrite the files
python3 scripts/update-po.py --check  # exit 1 if anything is missing or untranslated
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fwmanager.i18n_build import extract_msgids, parse_po, write_po  # noqa: E402

LANGS = ("pt_PT", "en", "es", "fr")


def main() -> int:
    msgids = extract_msgids(ROOT / "src/fwmanager", ROOT / "src/fwmanager/data/services.toml")
    check = "--check" in sys.argv
    missing = 0
    write_po(ROOT / "po/fwmanager.pot", msgids, {}, "")
    for lang in LANGS:
        po = ROOT / "po" / f"{lang}.po"
        existing = parse_po(po.read_text(encoding="utf-8")) if po.exists() else {}
        if lang == "en":  # English is the source language: identity translations
            existing = {m: existing.get(m) or (list(m) if isinstance(m, tuple) else m)
                        for m in msgids}  # fmt: skip
        todo = [m for m in msgids if not existing.get(m)]
        missing += len(todo)
        for m in todo:
            print(f"{lang}: untranslated: {str(m)[:70]!r}")
        if not check:
            write_po(po, msgids, existing, lang)
    return 1 if check and missing else 0


if __name__ == "__main__":
    sys.exit(main())
