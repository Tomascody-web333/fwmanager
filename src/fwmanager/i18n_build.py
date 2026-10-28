"""Tiny pure-Python gettext toolchain: extract msgids, read .po, write .mo.

Avoids a dependency on the gettext tools at install time. Supports what we use: plain
msgid/msgstr pairs, plurals (msgid_plural / msgstr[n], from ``_n()``), no contexts, and the
usual C-style escapes.

A plural entry is keyed by the tuple ``(singular, plural)`` and translated by a list of forms.
"""

from __future__ import annotations

import ast
import struct
import sys
import tomllib
from pathlib import Path

MARKERS = {"_", "N_"}
PLURAL_MARKERS = {"_n"}
HEADER = (
    "Content-Type: text/plain; charset=UTF-8\n"
    "Content-Transfer-Encoding: 8bit\n"
)  # fmt: skip
# How many forms a language has and which one a number takes (the gettext header syntax).
PLURAL_FORMS = {
    "en": "nplurals=2; plural=(n != 1);",
    "pt_PT": "nplurals=2; plural=(n != 1);",
    "es": "nplurals=2; plural=(n != 1);",
    "fr": "nplurals=2; plural=(n > 1);",
}

MsgId = str | tuple[str, str]


def _literal(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value:
        return node.value
    return None


def _key(msgid: MsgId) -> tuple:
    return (msgid, "") if isinstance(msgid, str) else msgid


def extract_msgids(src_dir: Path, services_toml: Path | None = None) -> list[MsgId]:
    """Every literal passed to ``_()`` / ``N_()`` (plain) or ``_n()`` (singular, plural), plus
    the service titles, sorted."""
    found: set[MsgId] = set()
    for path in sorted(Path(src_dir).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id in MARKERS and node.args and _literal(node.args[0]):
                found.add(_literal(node.args[0]))
            elif node.func.id in PLURAL_MARKERS and len(node.args) >= 2:
                one, many = _literal(node.args[0]), _literal(node.args[1])
                if one and many:
                    found.add((one, many))
    if services_toml and Path(services_toml).exists():
        data = tomllib.loads(Path(services_toml).read_text(encoding="utf-8"))
        found.update(v["title"] for v in data.values() if "title" in v)
    return sorted(found, key=_key)


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


def parse_po(text: str) -> dict:
    """``{msgid: msgstr}`` and ``{(singular, plural): [forms]}``; fuzzy entries and empty
    translations are skipped (a plural entry counts only with every form filled in)."""
    entries: dict = {}
    parts: dict[str, list[str]] = {}
    fuzzy = pending_fuzzy = False
    current: str | None = None

    def flush() -> None:
        if "id" not in parts or fuzzy:
            return
        key = "".join(parts["id"])
        if "plural" in parts:
            forms = ["".join(parts[f"str{i}"]) for i in range(len(parts)) if f"str{i}" in parts]
            if forms and all(forms):
                entries[(key, "".join(parts["plural"]))] = forms
        elif "str" in parts:
            val = "".join(parts["str"])
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
            parts, current = {"id": [_unquote(line[6:])]}, "id"
            fuzzy, pending_fuzzy = pending_fuzzy, False
        elif line.startswith("msgid_plural "):
            parts["plural"], current = [_unquote(line[13:])], "plural"
        elif line.startswith("msgstr["):
            index, rest = line[7:].split("]", 1)
            current = f"str{int(index)}"
            parts[current] = [_unquote(rest)]
        elif line.startswith("msgstr "):
            parts["str"], current = [_unquote(line[7:])], "str"
        elif line.startswith('"') and current:
            parts[current].append(_unquote(line))
        else:
            raise ValueError(f"unexpected line in .po: {line[:40]!r}")
    flush()
    return entries


def header(lang: str) -> str:
    text = HEADER + f"Language: {lang}\n"
    if lang in PLURAL_FORMS:
        text += f"Plural-Forms: {PLURAL_FORMS[lang]}\n"
    return text


def nplurals(lang: str) -> int:
    return int(PLURAL_FORMS.get(lang, "nplurals=2;").split("nplurals=")[1].split(";")[0])


def write_po(path: Path, msgids: list[MsgId], existing: dict, lang: str) -> None:
    lines = [
        f"# Translations for fwmanager ({lang}).",
        'msgid ""',
        f"msgstr {_quote(header(lang))}",
        "",
    ]
    for mid in msgids:
        if isinstance(mid, tuple):
            forms = list(existing.get(mid) or [])
            forms += [""] * (nplurals(lang) - len(forms))
            lines += [f"msgid {_quote(mid[0])}", f"msgid_plural {_quote(mid[1])}"]
            lines += [f"msgstr[{i}] {_quote(form)}" for i, form in enumerate(forms)]
            lines.append("")
        else:
            lines += [f"msgid {_quote(mid)}", f"msgstr {_quote(existing.get(mid, ''))}", ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def compile_mo(entries: dict) -> bytes:
    """Serialise to the GNU .mo format (little endian, no hash table)."""
    flat: dict[str, str] = {}
    for key, value in entries.items():
        if isinstance(key, tuple):  # plural: "singular\0plural" -> "form0\0form1..."
            flat["\0".join(key)] = "\0".join(value)
        else:
            flat[key] = value
    if "charset" not in flat.get("", ""):
        flat[""] = HEADER + flat.get("", "")  # without it gettext assumes ASCII
    entries = dict(sorted(flat.items()))
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
