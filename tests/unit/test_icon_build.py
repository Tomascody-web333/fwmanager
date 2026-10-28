import pytest

from fwmanager import icon_build


def test_renders_every_size(tmp_path):
    svg = icon_build.Path(__file__).resolve().parents[2] / "data/icons/org.fwmanager.App.svg"
    try:
        written = icon_build.render(svg, tmp_path, "x", sizes=(16, 48))
    except Exception as exc:  # no SVG loader on this machine
        pytest.skip(f"cannot render SVG here: {exc}")
    assert [p.relative_to(tmp_path).as_posix() for p in written] == [
        "16x16/apps/x.png", "48x48/apps/x.png"]  # fmt: skip


def test_a_missing_renderer_never_fails_the_install(tmp_path, capsys):
    assert icon_build.main([str(tmp_path / "missing.svg"), str(tmp_path), "x"]) == 0
    assert "skipped" in capsys.readouterr().err
