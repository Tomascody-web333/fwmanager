"""Settings → Backup (export and import) and Networks → the router of each network."""

import pytest
from helpers.shot import pump
from test_windows import daemon, env  # noqa: F401

pytestmark = pytest.mark.ui

UUID = "123e4567-e89b-12d3-a456-426614174000"


def test_export_writes_a_file_only_you_can_read(env, tmp_path):  # noqa: F811
    target = tmp_path / "out.json"
    env.win.settings._exported(str(target), env.daemon.exported, None)
    assert target.read_text() == env.daemon.exported
    assert target.stat().st_mode & 0o077 == 0  # it lists the networks you use


def test_import_asks_first_then_replaces(env, tmp_path):  # noqa: F811
    s = env.win.settings
    s.confirm_import('{"format": "fwmanager-settings", "version": 1}')
    pump(0.2)
    assert env.daemon.imported == []  # nothing happens before the answer
    s.import_dialog.emit("response", "cancel")
    assert env.daemon.imported == []
    s.confirm_import('{"format": "fwmanager-settings", "version": 1}')
    s.import_dialog.emit("response", "import")
    assert env.daemon.imported == ['{"format": "fwmanager-settings", "version": 1}']


def test_unreadable_files_are_refused_before_the_service_sees_them(gtk, tmp_path):
    from fwmanager.ui.pages import settings as page  # GTK only once the display is up

    assert page.read_settings_file(str(tmp_path / "missing.json")) is None
    binary = tmp_path / "x.bin"
    binary.write_bytes(b"\xff\xfe\x00garbage")
    assert page.read_settings_file(str(binary)) is None


def test_the_router_line_and_the_trust_button(env):  # noqa: F811
    env.daemon.routers[("Casa WiFi", UUID)] = ["aa:bb:cc:00:11:22"]
    env.daemon.unrecognised[("Casa WiFi", UUID)] = "aa:bb:cc:00:11:99"
    net = env.win.networks
    net.load()
    pump(0.2)
    row = net.rows_by_network[("Casa WiFi", UUID)]
    assert "aa:bb:cc:00:11:99" in row.get_subtitle() and "Public" in row.get_subtitle()
    trust = net.trust_buttons[("Casa WiFi", UUID)]
    assert trust.get_visible()
    trust.emit("clicked")
    pump(0.2)
    assert env.daemon.routers[("Casa WiFi", UUID)][-1] == "aa:bb:cc:00:11:99"
    row = net.rows_by_network[("Casa WiFi", UUID)]
    assert row.get_subtitle() == "Router recognised."
    assert not net.trust_buttons[("Casa WiFi", UUID)].get_visible()


def test_a_network_whose_router_was_not_seen_yet(env):  # noqa: F811
    net = env.win.networks
    net.load()
    pump(0.2)
    assert "not seen yet" in net.rows_by_network[("Casa WiFi", UUID)].get_subtitle()
