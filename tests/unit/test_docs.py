"""The documents are in English only, and their links work."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def documents():
    yield ROOT / "README.md"
    yield from sorted((ROOT / "docs").glob("*.md"))


def test_there_are_no_portuguese_copies_left():
    assert list(ROOT.glob("**/*.pt.md")) == [] or all(
        ".venv" in str(p) for p in ROOT.glob("**/*.pt.md")
    )
    for doc in documents():
        text = doc.read_text()
        assert ".pt.md" not in text and "Português](" not in text, doc.name


def test_local_links_resolve():
    found = list(documents())
    assert len(found) >= 3
    for doc in found:
        for target in re.findall(r"\]\(((?!http|#)[^)#]+)", doc.read_text()):
            assert (doc.parent / target).exists(), f"{doc.name}: {target}"


def test_the_readme_tells_about_every_feature_of_this_version():
    text = (ROOT / "README.md").read_text()
    for needle in (
        "Backup",
        "trust-router",
        "fwmanager --background",
        "fwmanager-cli export",
        "Spanish",
        "French",
        "Releasing a new version",
    ):
        assert needle in text, needle
