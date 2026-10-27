"""Every document ships in English and Portuguese, linked to each other."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def pairs():
    yield ROOT / "README.md", ROOT / "README.pt.md"
    for en in sorted((ROOT / "docs").glob("*.md")):
        if not en.name.endswith(".pt.md"):
            yield en, en.with_name(en.stem + ".pt.md")


def test_every_document_has_both_languages_and_links_them():
    found = list(pairs())
    assert len(found) >= 3
    for en, pt in found:
        assert pt.is_file(), f"missing {pt.name}"
        assert pt.name in en.read_text(), f"{en.name} does not link {pt.name}"
        assert en.name in pt.read_text(), f"{pt.name} does not link {en.name}"


def test_local_links_resolve():
    import re

    for en, pt in pairs():
        for doc in (en, pt):
            for target in re.findall(r"\]\(((?!http|#)[^)#]+)", doc.read_text()):
                assert (doc.parent / target).exists(), f"{doc.name}: {target}"
