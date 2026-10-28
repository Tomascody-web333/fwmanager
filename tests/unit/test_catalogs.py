"""The PT-PT, ES, FR and EN catalogues stay complete, consistent and loadable."""

import gettext
import re
import subprocess
import sys
from pathlib import Path

import pytest

from fwmanager import i18n_build as ib

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src/fwmanager"
TOML = SRC / "data/services.toml"
PLACEHOLDER = re.compile(r"\{[^{}]*\}")

# Technical names that are the same in Portuguese.
SAME_IN_PT = {"SSH", "FTP", "MQTT", "NFS", "Syncthing", "KDE Connect", "fwmanager", "Firewall",
              "{before} → {after}", "Wi-Fi: {name}", "Firewall: {state}", "info", "n",
              "local"}  # fmt: skip


@pytest.fixture(scope="module")
def msgids():
    return ib.extract_msgids(SRC, TOML)


@pytest.fixture(scope="module")
def pt():
    return ib.parse_po((ROOT / "po/pt_PT.po").read_text(encoding="utf-8"))


def _forms(m):
    return list(m) if isinstance(m, tuple) else [m]


@pytest.fixture(scope="module", params=["es", "fr"])
def other(request):
    return request.param, ib.parse_po((ROOT / f"po/{request.param}.po").read_text(encoding="utf-8"))


def test_scripts_check_passes():
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts/update-po.py"), "--check"],
        capture_output=True, text=True,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout


def test_every_string_is_translated_to_portuguese(msgids, pt):
    assert [m for m in msgids if not pt.get(m)] == []


def test_no_stale_translations(msgids, pt):
    assert sorted(k for k in pt if k and k not in msgids) == []


def test_placeholders_match_exactly(msgids, pt):
    for m in msgids:
        if isinstance(m, tuple):  # plurals: a singular form may drop {n}
            for form in pt[m]:
                got = set(PLACEHOLDER.findall(form))
                assert got <= set(PLACEHOLDER.findall(m[1])), m
            continue
        assert sorted(PLACEHOLDER.findall(m)) == sorted(PLACEHOLDER.findall(pt[m])), m


def test_portuguese_is_really_translated(msgids, pt):
    untouched = [m for m in msgids if not isinstance(m, tuple) and pt[m] == m]
    untouched = [m for m in untouched if m not in SAME_IN_PT]
    assert untouched == []


def test_english_catalogue_is_identity(msgids):
    en = ib.parse_po((ROOT / "po/en.po").read_text(encoding="utf-8"))
    assert all(en[m] == (list(m) if isinstance(m, tuple) else m) for m in msgids)


def test_pot_is_current(msgids):
    pot = ib.parse_po((ROOT / "po/fwmanager.pot").read_text(encoding="utf-8"))
    # the .pot has empty msgstr, which parse_po skips; compare msgids textually instead
    text = (ROOT / "po/fwmanager.pot").read_text(encoding="utf-8")
    assert text.count("\nmsgid ") - 1 == len(msgids)
    assert pot == {"": pot[""]}


def test_european_portuguese_not_brazilian(pt):
    """Spot-check vocabulary: PT-PT, not PT-BR."""
    flat = [t for v in pt.values() for t in (v if isinstance(v, list) else [v])]
    joined = " ".join(flat).lower()
    for brazilian in ("tela", "arquivo ", "usuário", "rede wi-fi", "desktop", "senha "):
        assert brazilian not in joined, brazilian
    assert "ficheiros" in joined and "utilizador" in joined and "palavra-passe" in joined


def test_catalogues_compile_and_load(tmp_path):
    ib.compile_dir(ROOT / "po", tmp_path)
    for lang in ("pt_PT", "en"):
        mo = tmp_path / lang / "LC_MESSAGES/fwmanager.mo"
        tr = gettext.GNUTranslations(mo.open("rb"))
        assert tr.charset() == "UTF-8"
    pt = gettext.GNUTranslations((tmp_path / "pt_PT/LC_MESSAGES/fwmanager.mo").open("rb"))
    assert pt.gettext("Settings") == "Definições"
    assert pt.gettext("Home") == "Casa"
    assert pt.gettext("File sharing (Samba)") == "Partilha de ficheiros (Samba)"


# -- Spanish and French ---

SAME_EVERYWHERE = SAME_IN_PT | {
    "FILE", "MQTT", "NFS", "Syncthing", "SSH", "FTP", "KDE Connect", "Error: {msg}",
    "%(prog)s: error: %(message)s\n", "{p}", "Router: not seen yet",
    # the same word in Spanish or French
    "Error", "General", "error", "no", "Notifications", "Options", "options", "Public", "ACTION",
    "SERVICE", "Service", "Note", "Wi-Fi: {name}",
}  # fmt: skip


def test_every_string_is_translated(msgids, other):
    lang, tr = other
    assert [m for m in msgids if not tr.get(m)] == [], lang


def test_nothing_stale(msgids, other):
    lang, tr = other
    assert sorted(str(k) for k in tr if k and k not in msgids) == [], lang


def test_placeholders_survive(msgids, other):
    lang, tr = other
    for m in msgids:
        forms = tr[m] if isinstance(m, tuple) else [tr[m]]
        want = set(PLACEHOLDER.findall(" ".join(_forms(m))))
        for text in forms:
            got = set(PLACEHOLDER.findall(text))
            assert got <= want and (want - got) <= {"{n}"}, (lang, m)


def test_really_translated(msgids, other):
    lang, tr = other
    untouched = [m for m in msgids if not isinstance(m, tuple) and tr[m] == m]
    assert [m for m in untouched if m not in SAME_EVERYWHERE] == [], lang


def test_plural_entries_have_the_right_number_of_forms(msgids, other):
    lang, tr = other
    for m in msgids:
        if isinstance(m, tuple):
            assert len(tr[m]) == 2 and tr[m][0] != tr[m][1], (lang, m)


def test_the_plural_rule_of_each_language(tmp_path):
    ib.compile_dir(ROOT / "po", tmp_path)
    from fwmanager.i18n import _n, set_language

    one = "{n} attempt was blocked. The latest: {src} to port {port}."
    many = "{n} attempts were blocked. The latest: {src} to port {port}."
    try:
        for lang, forms in {
            "en": (one, many, many),
            "pt": ("{n} tentativa", "{n} tentativas", "{n} tentativas"),
            "es": ("{n} intento", "{n} intentos", "{n} intentos"),
            "fr": ("{n} tentative a", "{n} tentatives ont", "{n} tentatives ont"),
        }.items():
            set_language(lang, str(tmp_path))
            # French counts 0 and 1 as singular; the others only 1.
            zero = forms[0] if lang == "fr" else forms[2]
            assert _n(one, many, 1).startswith(forms[0]), lang
            assert _n(one, many, 5).startswith(forms[1]), lang
            assert _n(one, many, 0).startswith(zero), lang
    finally:
        set_language(None)


def test_each_language_has_its_own_vocabulary(other):
    lang, tr = other
    expected = {"es": ("Ajustes", "Reglas"), "fr": ("Réglages", "Règles")}[lang]
    assert tr["Settings"] == expected[0] and tr["Rules"] == expected[1]


def test_compiled_catalogues_load_for_all_languages(tmp_path):
    ib.compile_dir(ROOT / "po", tmp_path)
    for lang, word in {"es": "Ajustes", "fr": "Réglages", "pt_PT": "Definições"}.items():
        tr = gettext.GNUTranslations((tmp_path / lang / "LC_MESSAGES/fwmanager.mo").open("rb"))
        assert tr.charset() == "UTF-8" and tr.gettext("Settings") == word


def test_plural_forms_survive_a_po_round_trip(tmp_path):
    entries = {("{n} a", "{n} as"): ["{n} x", "{n} xs"], "B": "b"}
    po = tmp_path / "fr.po"
    ib.write_po(po, [("{n} a", "{n} as"), "B"], entries, "fr")
    assert ib.parse_po(po.read_text()).get(("{n} a", "{n} as")) == ["{n} x", "{n} xs"]
    assert "Plural-Forms: nplurals=2; plural=(n > 1);" in po.read_text()
