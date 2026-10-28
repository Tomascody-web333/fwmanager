import gettext
import subprocess
import sys
from pathlib import Path

import pytest

from fwmanager import i18n
from fwmanager import i18n_build as ib

ROOT = Path(__file__).resolve().parents[2]


def test_extract_finds_marked_strings_only(tmp_path):
    (tmp_path / "a.py").write_text(
        "from x import _, N_\n"
        'print(_("Hello"), N_("Later"), other("Skip"), _(variable), _("Multi" "part"))\n'
        'class C:\n    def f(self):\n        return _("Inside")\n'
    )
    assert ib.extract_msgids(tmp_path) == ["Hello", "Inside", "Later", "Multipart"]


def test_extract_includes_service_titles(tmp_path):
    toml = tmp_path / "s.toml"
    toml.write_text('[ssh]\ntitle = "SSH"\nports = ["22/tcp"]\n[x]\nports = ["1/tcp"]\n')
    assert ib.extract_msgids(tmp_path, toml) == ["SSH"]


PO = r"""# comment
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\n"

msgid "Hello"
msgstr "Olá"

msgid "Quote \" and \\ and \n newline"
msgstr "Aspas \" e \\ e \n linha"

msgid "Long "
"string"
msgstr "Texto "
"longo"

#, fuzzy
msgid "Fuzzy"
msgstr "Duvidoso"

msgid "Untranslated"
msgstr ""

msgid "Ação ✓"
msgstr "Acção ✓"
"""


def test_parse_po():
    got = ib.parse_po(PO)
    assert got["Hello"] == "Olá"
    assert got['Quote " and \\ and \n newline'] == 'Aspas " e \\ e \n linha'
    assert got["Long string"] == "Texto longo"
    assert "Fuzzy" not in got and "Untranslated" not in got
    assert got["Ação ✓"] == "Acção ✓"


@pytest.mark.parametrize("bad", ["garbage", 'msgid "unterminated', "msgid Hello"])
def test_parse_po_rejects_garbage(bad):
    with pytest.raises(ValueError):
        ib.parse_po(bad)


def test_compiled_mo_is_readable_by_python_gettext(tmp_path):
    entries = ib.parse_po(PO)
    mo = tmp_path / "t.mo"
    mo.write_bytes(ib.compile_mo(entries))
    with mo.open("rb") as fh:
        tr = gettext.GNUTranslations(fh)
    assert tr.gettext("Hello") == "Olá"
    assert tr.gettext("Ação ✓") == "Acção ✓"
    assert tr.gettext("Not there") == "Not there"
    assert tr.gettext('Quote " and \\ and \n newline').startswith("Aspas")


def test_write_po_roundtrip(tmp_path):
    po = tmp_path / "x.po"
    ib.write_po(po, ["A", 'B "q"'], {"A": "Á"}, "pt_PT")
    assert ib.parse_po(po.read_text()) == {"": ib.header("pt_PT"), "A": "Á"}


def test_compile_dir_layout_and_cli(tmp_path):
    (tmp_path / "po").mkdir()
    (tmp_path / "po/pt_PT.po").write_text(PO)
    r = subprocess.run(
        [sys.executable, "-m", "fwmanager.i18n_build", "compile", str(tmp_path / "po"),
         str(tmp_path / "out")],
        capture_output=True, text=True, env={"PYTHONPATH": str(ROOT / "src")},
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "out/pt_PT/LC_MESSAGES/fwmanager.mo").is_file()
    assert subprocess.run([sys.executable, "-m", "fwmanager.i18n_build"],
                          env={"PYTHONPATH": str(ROOT / "src")}).returncode == 2  # fmt: skip


def test_set_language_loads_compiled_catalog(tmp_path):
    (tmp_path / "po").mkdir()
    (tmp_path / "po/pt_PT.po").write_text('msgid "Hello"\nmsgstr "Olá"\n')
    ib.compile_dir(tmp_path / "po", tmp_path / "loc")
    try:
        i18n.set_language("pt_PT.UTF-8", str(tmp_path / "loc"))
        assert i18n._("Hello") == "Olá" and i18n.current_language() == "pt"
        i18n.set_language("pt", str(tmp_path / "loc"))
        assert i18n._("Hello") == "Olá"
        i18n.set_language("en", str(tmp_path / "loc"))
        assert i18n._("Hello") == "Hello" and i18n.current_language() == "en"
    finally:
        i18n.set_language(None)


def test_compile_always_declares_utf8():
    tr = gettext.GNUTranslations(__import__("io").BytesIO(ib.compile_mo({"Hi": "Olá"})))
    assert tr.gettext("Hi") == "Olá" and tr.charset() == "UTF-8"
