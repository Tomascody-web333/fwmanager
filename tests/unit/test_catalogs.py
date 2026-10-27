"""The PT-PT and EN catalogues stay complete, consistent and loadable."""

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
              "{before} → {after}", "Wi-Fi: {name}"}  # fmt: skip


@pytest.fixture(scope="module")
def msgids():
    return ib.extract_msgids(SRC, TOML)


@pytest.fixture(scope="module")
def pt():
    return ib.parse_po((ROOT / "po/pt_PT.po").read_text(encoding="utf-8"))


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
        assert sorted(PLACEHOLDER.findall(m)) == sorted(PLACEHOLDER.findall(pt[m])), m


def test_portuguese_is_really_translated(msgids, pt):
    untouched = [m for m in msgids if pt[m] == m and m not in SAME_IN_PT]
    assert untouched == []


def test_english_catalogue_is_identity(msgids):
    en = ib.parse_po((ROOT / "po/en.po").read_text(encoding="utf-8"))
    assert all(en[m] == m for m in msgids)


def test_pot_is_current(msgids):
    pot = ib.parse_po((ROOT / "po/fwmanager.pot").read_text(encoding="utf-8"))
    # the .pot has empty msgstr, which parse_po skips; compare msgids textually instead
    text = (ROOT / "po/fwmanager.pot").read_text(encoding="utf-8")
    assert text.count("\nmsgid ") - 1 == len(msgids)
    assert pot == {"": pot[""]}


def test_european_portuguese_not_brazilian(pt):
    """Spot-check vocabulary: PT-PT, not PT-BR."""
    joined = " ".join(pt.values()).lower()
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
