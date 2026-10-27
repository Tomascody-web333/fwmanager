"""Tiny pure-Python gettext toolchain: extract msgids, read .po, write .mo.

Avoids a dependency on the gettext tools at install time. Supports what we use: plain
msgid/msgstr pairs (no plurals, no contexts) and the usual C-style escapes.
"""

from __future__ import annotations

import ast
import struct
import sys
import tomllib
from pathlib import Path

MARKERS = {"_", "N_"}
HEADER = (
    "Content-Type: text/plain; charset=UTF-8\n"
    "Content-Transfer-Encoding: 8bit\n"
)  # fmt: skip


def extract_msgids(src_dir: Path, services_toml: Path | None = None) -> list[str]:
    """Every literal passed to ``_()`` / ``N_()`` plus the service titles, sorted."""
    found: set[str] = set()
    for path in sorted(Path(src_dir).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in MARKERS
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value
            ):
                found.add(node.args[0].value)
    if services_toml and Path(services_toml).exists():
        data = tomllib.loads(Path(services_toml).read_text(encoding="utf-8"))
        found.update(v["title"] for v in data.values() if "title" in v)
    return sorted(found)


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _unquote(token: str) -> str:
    token = token.strip()
    if not (token.startswith('"') and token.endswith('"')):
        raise ValueError(f"not a quoted string: {token[:30]!r}")
    out, body, i = [], token[1:-1], 0
    escapes = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}
    while i < len(body):
        if body[i] == "\\" and i + 1 < len(body):
            out.append(escapes.get(body[i + 1], body[i + 1]))
            i += 2
        else:
            out.append(body[i])
            i += 1
    return "".join(out)


def parse_po(text: str) -> dict[str, str]:
    """``{msgid: msgstr}``; fuzzy entries and empty translations are skipped."""
    entries: dict[str, str] = {}
    msgid: list[str] | None = None
    msgstr: list[str] | None = None
    fuzzy = pending_fuzzy = False
    current = None

    def flush() -> None:
        if msgid is not None and msgstr is not None and not fuzzy:
            key, val = "".join(msgid), "".join(msgstr)
            if val or key == "":
                entries[key] = val

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#,") and "fuzzy" in line:
            pending_fuzzy = True
        elif line.startswith("#") or not line:
            continue
        elif line.startswith("msgid "):
            flush()
            msgid, msgstr, current = [_unquote(line[6:])], None, "id"
            fuzzy, pending_fuzzy = pending_fuzzy, False
        elif line.startswith("msgstr "):
            msgstr, current = [_unquote(line[7:])], "str"
        elif line.startswith('"'):
            (msgid if current == "id" else msgstr).append(_unquote(line))
        else:
            raise ValueError(f"unexpected line in .po: {line[:40]!r}")
    flush()
    return entries


def write_po(path: Path, msgids: list[str], existing: dict[str, str], lang: str) -> None:
    lines = [
        f"# Translations for fwmanager ({lang}).",
        'msgid ""',
        f"msgstr {_quote(HEADER + 'Language: ' + lang + chr(10))}",
        "",
    ]
    for mid in msgids:
        lines += [f"msgid {_quote(mid)}", f"msgstr {_quote(existing.get(mid, ''))}", ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def compile_mo(entries: dict[str, str]) -> bytes:
    """Serialise to the GNU .mo format (little endian, no hash table)."""
    entries = dict(entries)
    if "charset" not in entries.get("", ""):
        entries[""] = HEADER + entries.get("", "")  # without it gettext assumes ASCII
    entries = dict(sorted(entries.items()))
    ids = [k.encode("utf-8") for k in entries]
    strs = [v.encode("utf-8") for v in entries.values()]
    n = len(ids)
    keystart = 7 * 4 + 16 * n
    id_blob = b"\0".join(ids) + b"\0"
    valuestart = keystart + len(id_blob)
    offsets, pos = [], 0
    for b in ids:
        offsets.append((len(b), keystart + pos))
        pos += len(b) + 1
    voffsets, pos = [], 0
    for b in strs:
        voffsets.append((len(b), valuestart + pos))
        pos += len(b) + 1
    out = struct.pack("<7I", 0x950412DE, 0, n, 7 * 4, 7 * 4 + 8 * n, 0, 0)
    for length, off in offsets:
        out += struct.pack("<2I", length, off)
    for length, off in voffsets:
        out += struct.pack("<2I", length, off)
    return out + id_blob + b"\0".join(strs) + b"\0"


def compile_dir(po_dir: Path, out_dir: Path, domain: str = "fwmanager") -> list[Path]:
    """Compile every ``<lang>.po`` into ``<out>/<lang>/LC_MESSAGES/<domain>.mo``."""
    written = []
    for po in sorted(Path(po_dir).glob("*.po")):
        target = Path(out_dir) / po.stem / "LC_MESSAGES" / f"{domain}.mo"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(compile_mo(parse_po(po.read_text(encoding="utf-8"))))
        written.append(target)
    return written


def main(argv: list[str] | None = None) -> int:
    """``python -m fwmanager.i18n_build compile PO_DIR OUT_DIR``"""
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 3 and argv[0] == "compile":
        for path in compile_dir(Path(argv[1]), Path(argv[2])):
            print(path)
        return 0
    print("usage: python -m fwmanager.i18n_build compile PO_DIR OUT_DIR", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
