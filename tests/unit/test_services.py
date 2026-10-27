import pytest

from fwmanager.core.services import load_builtin, load_catalog, parse_catalog
from fwmanager.errors import ValidationError


def test_builtin_catalog_has_required_services():
    cat = load_builtin()
    for sid in ("ssh", "samba", "kdeconnect", "syncthing", "ipp", "web"):
        assert sid in cat
    ssh = cat["ssh"]
    assert [(p.first, p.last, p.proto) for p in ssh.ports] == [(22, 22, "tcp")]
    assert {(p.first, p.proto) for p in cat["samba"].ports} == {(139, "tcp"), (445, "tcp")}
    assert {(p.first, p.last) for p in cat["kdeconnect"].ports} == {(1714, 1764)}
    assert {(p.first, p.proto) for p in cat["syncthing"].ports} == {
        (22000, "tcp"),
        (22000, "udp"),
        (21027, "udp"),
    }


@pytest.mark.parametrize(
    "bad",
    [
        {"Bad": {"ports": ["22/tcp"]}},
        {'x"y': {"ports": ["22/tcp"]}},
        {"x": {"ports": []}},
        {"x": {"ports": ["99999/tcp"]}},
        {"x": {"ports": ["22/tcp"], "extra": 1}},
        {"x": {"title": "", "ports": ["22/tcp"]}},
        {"x": {"title": "t" * 200, "ports": ["22/tcp"]}},
        {"x": ["22/tcp"]},
        ["x"],
    ],
)
def test_parse_catalog_rejects(bad):
    with pytest.raises(ValidationError):
        parse_catalog(bad)


def test_extension_file(tmp_path):
    f = tmp_path / "extra.toml"
    f.write_text('[plex]\ntitle = "Plex"\nports = ["32400/tcp"]\n')
    assert "plex" in load_catalog([f])


def test_extension_cannot_override_builtin(tmp_path):
    f = tmp_path / "extra.toml"
    f.write_text('[ssh]\nports = ["2222/tcp"]\n')
    with pytest.raises(ValidationError):
        load_catalog([f])


def test_extension_garbage_rejected(tmp_path):
    f = tmp_path / "bad.toml"
    f.write_text("this is = = not toml")
    with pytest.raises(ValidationError):
        load_catalog([f])
    big = tmp_path / "big.toml"
    big.write_text("# " + "x" * 70000)
    with pytest.raises(ValidationError):
        load_catalog([big])
