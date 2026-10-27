from fwmanager.core.config import resolve_language, system_language
from fwmanager.core.conflicts import detect, ufw_enabled


def active(*units):
    return lambda u: u in units


def test_no_conflicts():
    c = detect(active(), ufw_conf="/nonexistent")
    assert c.blocking == () and c.warnings == ()


def test_firewalld_blocks():
    assert detect(active("firewalld.service")).blocking == ("firewalld",)


def test_ufw_blocks_only_when_enabled(tmp_path):
    conf = tmp_path / "ufw.conf"
    conf.write_text("# x\nENABLED=no\n")
    assert detect(active("ufw.service"), ufw_conf=conf).blocking == ()
    conf.write_text("ENABLED=yes\n")
    assert detect(active("ufw.service"), ufw_conf=conf).blocking == ("ufw",)
    assert detect(active("ufw.service"), ufw_conf=tmp_path / "missing").blocking == ("ufw",)
    assert ufw_enabled(conf) is not False


def test_docker_libvirt_are_warnings_only():
    c = detect(active("docker.service", "libvirtd.service"))
    assert c.blocking == () and c.warnings == ("docker", "libvirt")


def test_warnings_from_foreign_tables():
    class FakeNft:
        def run(self, args):
            return "table ip docker-bridges\ntable ip libvirt_network\ntable inet fwmanager\n"

    c = detect(active(), nft=FakeNft())
    assert c.warnings == ("docker", "libvirt")


def test_nft_failure_is_ignored():
    from fwmanager.errors import NftError

    class Boom:
        def run(self, args):
            raise NftError("no")

    assert detect(active(), nft=Boom()).warnings == ()


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
