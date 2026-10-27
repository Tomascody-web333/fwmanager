"""Text nobody vetted (Wi-Fi names come from any access point in range) is shown as typed.

libadwaita treats row, toast, group and status-page text as Pango markup unless told not to.
"""

import pytest
from helpers.gtk_tools import texts
from helpers.shot import pump
from test_windows import daemon, env  # noqa: F401
from test_wizard import Env

from fwmanager.ui.netinfo import WIFI, CurrentNetwork

pytestmark = pytest.mark.ui

HOSTILE = ["Café & Bar", "<b>BOLD</b>", "<span size='90000'>X</span>", "a<b", "&amp;"]


def test_saved_network_names_are_shown_literally(env):  # noqa: F811
    env.daemon.networks = [(s, "", "casa") for s in HOSTILE]
    env.win.views.set_visible_child_name("networks")
    pump(0.4)
    shown = texts(env.win.networks)
    assert [s for s in HOSTILE if s not in shown] == []
    assert "BOLD" not in shown and "&" not in shown  # what markup would have made of them


@pytest.mark.parametrize("ssid", HOSTILE)
def test_the_current_network_in_the_wizard_is_shown_literally(gtk, ssid):
    e = Env(gtk, stage="profile")
    try:
        net = CurrentNetwork(ssid, ssid, "123e4567-e89b-12d3-a456-426614174000", WIFI)
        e.controller.current_network = lambda done, bus_address=None: done(net, None)
        e.wizard.go("profile")
        pump(0.3)
        assert f"Wi-Fi: {ssid}" in texts(e.wizard)
    finally:
        e.close()


def test_rows_groups_status_pages_and_toasts_do_not_parse_markup(gtk):
    from gi.repository import Adw

    import fwmanager.ui.common  # noqa: F401  (switches markup off)

    for cls in (Adw.ActionRow, Adw.SwitchRow, Adw.ComboRow, Adw.EntryRow, Adw.ExpanderRow):
        assert cls(title="a & b").get_use_markup() is False, cls
    assert Adw.Toast(title="a & b").get_use_markup() is False
    # A group hands back the text it shows: empty when "a & b" was read as broken markup.
    group = Adw.PreferencesGroup(title="a & b", description="<b>")
    assert group.get_title() == "a & b" and group.get_description() == "<b>"
    group.set_description("x & y")
    assert group.get_description() == "x & y"
    assert Adw.StatusPage(description="1 < 2").get_description() == "1 &lt; 2"  # shown: 1 < 2
