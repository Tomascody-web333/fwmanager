import pytest

from fwmanager.core.config import resolve_language, system_language
from fwmanager.core.conflicts import detect


def active(*units):
    return lambda u: u in units


def test_no_conflicts():
    c = detect(active())
    assert c.blocking == () and c.warnings == ()


def test_firewalld_blocks():
    assert detect(active("firewalld.service")).blocking == ("firewalld",)


def test_ufw_is_the_engine_not_a_conflict():
    assert detect(active("ufw.service")).blocking == ()


def test_docker_libvirt_are_warnings_only():
    c = detect(active("docker.service", "libvirtd.service"))
    assert c.blocking == () and c.warnings == ("docker", "libvirt")


def test_language_priority(tmp_path):
    system = tmp_path / "config.toml"
    user = tmp_path / "settings.toml"
    assert resolve_language(None, tmp_path / "none", env_lang="pt_PT.UTF-8") == "pt"
    assert resolve_language(None, tmp_path / "none", env_lang="de_DE.UTF-8") == "en"
    assert resolve_language(None, tmp_path / "none", env_lang="") == "en"
    system.write_text('lang = "en"\n')
    assert resolve_language(None, system, env_lang="pt_PT") == "en"
    user.write_text('lang = "pt"\n')
    assert resolve_language(user, system, env_lang="en_US") == "pt"


def test_bad_language_files_fall_through(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("= = =")
    other = tmp_path / "other.toml"
    other.write_text('lang = "klingon"\n')
    assert resolve_language(bad, other, env_lang="pt") == "pt"
    assert system_language("PT") == "pt"


def test_a_system_config_that_is_not_utf8_is_ignored(tmp_path):
    bad = tmp_path / "config.toml"
    bad.write_bytes(b'lang = "\xff"')
    assert resolve_language(None, bad, env_lang="pt_PT.UTF-8") == "pt"


@pytest.mark.parametrize(
    "env,expected",
    [("es_ES.UTF-8", "es"), ("fr_FR.UTF-8", "fr"), ("pt_PT", "pt"), ("de_DE", "en"), ("", "en")],
)
def test_system_language_knows_the_four_languages(env, expected):
    assert system_language(env) == expected
