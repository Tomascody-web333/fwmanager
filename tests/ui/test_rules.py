"""The Rules page and the rule editor."""

import pytest
from helpers.fake_client import FakeClient, FakeDaemon
from helpers.gtk_tools import texts
from helpers.shot import pump

from fwmanager.core import rulebook as rb
from fwmanager.errors import FwError
from fwmanager.i18n import set_language
from fwmanager.ui import rule_text
from fwmanager.ui.controller import Controller
from fwmanager.ui.worker import run_sync

pytestmark = pytest.mark.ui

TITLES = dict(FakeClient().list_services())


@pytest.fixture(autouse=True)
def english():
    set_language(None)
    yield
    set_language(None)


# -- the texts ---


@pytest.mark.parametrize(
    "template,params,expected",
    [
        (rb.ALLOW_ONLY_IN, {"service": "samba", "profiles": ["casa"]},
         "Allow File sharing (Samba) only in: Home"),
        (rb.ALLOW_ONLY_IN, {"service": "ssh", "profiles": ["casa", "trabalho"]},
         "Allow SSH only in: Home, Work"),
        (rb.BLOCK_ALL_EXCEPT, {"services": ["ssh"], "profiles": ["publico"]},
         "Block everything coming in, except SSH, in: Public"),
        (rb.BLOCK_ALL_EXCEPT, {"services": ["ssh", "web"], "profiles": ["publico"]},
         "Block everything coming in, except SSH, Web server (HTTP/HTTPS), in: Public"),
        (rb.BLOCK_ALL_EXCEPT, {"services": [], "profiles": ["publico"]},
         "Block everything coming in, in: Public"),
        (rb.RESTRICT_SOURCE, {"scope": "lan", "profiles": ["publico"]},
         "Only accept connections from the local network in: Public"),
        (rb.RESTRICT_SOURCE, {"scope": "cidrs", "cidrs": ["10.0.0.0/8"], "profiles": ["casa"]},
         "Only accept connections from 10.0.0.0/8 in: Home"),
        (rb.HIDE, {"profiles": ["publico"]}, "Hide this computer (no ping, no mDNS) in: Public"),
        (rb.CLOSE_SERVICE, {"service": "ssh"}, "Close SSH in every profile"),
    ],
)  # fmt: skip
def test_sentences(template, params, expected):
    assert rule_text.sentence(template, params, TITLES) == expected


def test_every_template_has_a_title_and_a_sentence():
    for template in rb.TEMPLATE_ORDER:
        assert rule_text.template_title(template) != template


def test_preview_lines():
    changes = [
        ("trabalho", ["samba"], [], "", "", "", []),
        ("casa", [], ["samba"], "off", "off", "cidrs", ["10.0.0.0/8", "192.168.0.0/16"]),
        ("publico", [], [], "on", "on", "lan", []),
    ]
    lines = rule_text.preview_lines(changes, TITLES)
    assert lines[0] == "Work: opens File sharing (Samba)"
    assert lines[1] == (
        "Home: closes File sharing (Samba); stops answering ping; turns mDNS off; "
        "only accepts connections from 10.0.0.0/8, 192.168.0.0/16"
    )
    assert lines[2] == (
        "Public: starts answering ping; turns mDNS on; only accepts connections "
        "from the local network"
    )
    assert rule_text.preview_lines([], TITLES) == [
        "Nothing changes: the profiles already work like this."
    ]


# -- the page and the editor ---


@pytest.fixture
def env(gtk, tmp_path):
    from gi.repository import Adw

    from fwmanager.ui import settings as us
    from fwmanager.ui.window import MainWindow

    daemon = FakeDaemon()
    controller = Controller(lambda: FakeClient(daemon), run=run_sync, user="ana")
    app = Adw.Application(application_id="org.fwmanager.TestRules")
    app.register()
    win = MainWindow(
        app, controller, us.UserSettings(setup_stage="done"), lambda c: None, lambda b: None
    )
    win.present()
    pump(0.3)

    class Env:
        pass

    e = Env()
    e.win, e.daemon, e.controller = win, daemon, controller
    yield e
    controller.stop_polling()
    win.destroy()


def open_editor(env):
    env.win.rules.add_btn.emit("clicked")
    pump(0.3)
    dialogs = [w for w in _children(env.win) if w.__class__.__name__ == "RuleEditor"]
    return dialogs[-1] if dialogs else None


def _children(widget):
    """Adw.Dialog sits in the window's overlay; find it by walking down."""
    out, stack = [], [widget]
    while stack:
        w = stack.pop()
        out.append(w)
        child = w.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return out


def make_editor(env):
    from fwmanager.ui.dialogs import RuleEditor

    saved = []
    dialog = RuleEditor(env.controller, list(env.daemon.profiles), TITLES, lambda: saved.append(1))
    dialog.present(env.win)
    pump(0.2)
    dialog.saved = saved
    return dialog


def test_empty_page_explains_itself(env):
    env.win.rules.load()
    t = texts(env.win.rules)
    assert "No rules yet." in t and "Add a rule" in t
    assert any("until you apply a profile" in x for x in t)


def test_editor_starts_with_the_first_template_and_a_preview(env):
    d = make_editor(env)
    assert d.current == rb.ALLOW_ONLY_IN
    # default: service = first sorted ("File sharing (Samba)"), profile = Home
    assert d.sentence_row.get_title() == "Allow File sharing (Samba) only in: Home"
    assert d.save_btn.get_sensitive()
    assert "Home" in " ".join(texts(d.preview)) or texts(d.preview)


def test_editor_saves_the_chosen_rule(env):
    d = make_editor(env)
    d.service_combo.set_selected(d._service_ids.index("web"))
    d.profile_switches["trabalho"].set_active(True)
    d.profile_switches["casa"].set_active(False)
    assert d.sentence_row.get_title() == "Allow Web server (HTTP/HTTPS) only in: Work"
    d.save_btn.emit("clicked")
    pump(0.1)
    assert env.daemon.rules[0][1:] == (
        "allow_only_in",
        {"service": "web", "profiles": ["trabalho"]},
    )
    assert d.saved == [1]


def test_save_is_disabled_until_a_profile_is_chosen(env):
    d = make_editor(env)
    d.profile_switches["casa"].set_active(False)
    assert not d.save_btn.get_sensitive()
    assert d.sentence_row.get_title() == "Choose the details below."
    d.save_btn.emit("clicked")  # a forced click must not save anything
    assert env.daemon.rules == []
    d.profile_switches["publico"].set_active(True)
    assert d.save_btn.get_sensitive()


def test_each_template_builds_its_blanks(env):
    d = make_editor(env)
    d.template.set_selected(rb.TEMPLATE_ORDER.index(rb.BLOCK_ALL_EXCEPT))
    assert set(d.service_switches) == set(TITLES) and set(d.profile_switches) == set(
        env.daemon.profiles
    )
    d.service_switches["ssh"].set_active(True)
    assert d.sentence_row.get_title() == "Block everything coming in, except SSH, in: Home"

    d.template.set_selected(rb.TEMPLATE_ORDER.index(rb.HIDE))
    assert d.service_switches is None and d.profile_switches is not None

    d.template.set_selected(rb.TEMPLATE_ORDER.index(rb.CLOSE_SERVICE))
    assert d.profile_switches is None and d.service_combo is not None
    assert d.sentence_row.get_title().startswith("Close ")


def test_specific_networks_need_a_valid_list(env):
    d = make_editor(env)
    d.template.set_selected(rb.TEMPLATE_ORDER.index(rb.RESTRICT_SOURCE))
    assert not d.cidrs.get_visible() and d.save_btn.get_sensitive()  # the local network
    d.scope.set_selected(1)
    assert d.cidrs.get_visible() and not d.save_btn.get_sensitive()  # nothing typed yet
    d.cidrs.set_text("10.2.0.0/16, 192.168.9.0/24")
    assert d.save_btn.get_sensitive()
    assert "10.2.0.0/16, 192.168.9.0/24" in d.sentence_row.get_title()
    d.cidrs.set_text("not a network")  # the daemon refuses it: the dialog must not allow saving
    assert not d.save_btn.get_sensitive()
    assert any("cannot be saved" in x for x in texts(d.preview))


def test_preview_shows_what_changes(env):
    d = make_editor(env)
    d.service_combo.set_selected(d._service_ids.index("samba"))
    d.profile_switches["casa"].set_active(False)
    d.profile_switches["trabalho"].set_active(True)
    lines = texts(d.preview)
    assert "Work: opens File sharing (Samba)" in lines
    assert "Home: closes File sharing (Samba)" in lines


def test_preview_of_a_noop(env):
    d = make_editor(env)
    d.template.set_selected(rb.TEMPLATE_ORDER.index(rb.HIDE))
    d.profile_switches["casa"].set_active(False)
    d.profile_switches["publico"].set_active(True)  # publico already hides itself
    assert "Nothing changes: the profiles already work like this." in texts(d.preview)


def test_a_stale_preview_answer_is_ignored(env):
    d = make_editor(env)
    d._serial = 5
    d._previewed(3, [("casa", ["ssh"], [], "", "", "", [])], None)  # an old answer
    assert "Casa" not in " ".join(texts(d.preview))
    assert d.preview.get_visible()


def test_page_lists_and_removes_rules(env):
    env.daemon.rules = [("r1", "close_service", {"service": "ssh"})]
    env.win.rules.load()
    assert "Close SSH in every profile" in texts(env.win.rules)
    env.win.rules._rows[0].get_last_child()  # exists
    env.win.rules._c.remove_rule("r1", env.win.rules._removed)
    assert env.daemon.rules == [] and "No rules yet." in texts(env.win.rules)


def test_saving_refreshes_the_profile_descriptions(env):
    env.daemon.rules = []
    env.win.home.load()
    before = {r.name_: r.get_subtitle() for r in env.win.home._profile_rows}
    assert "Printer sharing (IPP)" in before["casa"]
    env.controller.add_rule(rb.CLOSE_SERVICE, {"service": "ipp"})
    env.win.rules._saved()
    after = {r.name_: r.get_subtitle() for r in env.win.home._profile_rows}
    assert "Printer sharing (IPP)" not in after["casa"]
    assert "Your rules change this profile." in after["casa"]


def test_daemon_errors_while_saving_keep_the_dialog_open(env):
    d = make_editor(env)
    env.daemon.error = FwError("x", code="too_many_rules")
    d.save_btn.set_sensitive(True)
    d.save_btn.emit("clicked")
    assert d.saved == [] and d.save_btn.get_sensitive()


# -- Portuguese: whole sentences, correct contractions ---


@pytest.fixture
def portuguese(tmp_path):
    from pathlib import Path

    from fwmanager import i18n_build as ib

    ib.compile_dir(Path(__file__).resolve().parents[2] / "po", tmp_path)
    set_language("pt", str(tmp_path))
    yield
    set_language(None)


def test_portuguese_sentences(portuguese):
    assert (
        rule_text.sentence(rb.ALLOW_ONLY_IN, {"service": "samba", "profiles": ["casa"]}, TITLES)
        == "Permitir Partilha de ficheiros (Samba) apenas em: Casa"
    )
    assert (
        rule_text.sentence(
            rb.BLOCK_ALL_EXCEPT, {"services": ["ssh"], "profiles": ["publico", "trabalho"]}, TITLES
        )
        == "Bloquear tudo o que entra, exceto SSH, em: Público, Trabalho"
    )
    assert rule_text.sentence(rb.CLOSE_SERVICE, {"service": "ssh"}, TITLES) == (
        "Fechar SSH em todos os perfis"
    )


def test_portuguese_preview_has_no_broken_contractions(portuguese):
    changes = [
        ("casa", [], ["samba"], "off", "", "lan", []),
        ("trabalho", ["ssh"], [], "", "on", "cidrs", ["10.0.0.0/8"]),
        ("publico", [], [], "", "", "any", []),
    ]
    lines = rule_text.preview_lines(changes, TITLES)
    assert lines[0] == (
        "Casa: fecha Partilha de ficheiros (Samba); deixa de responder a ping; "
        "só aceita ligações da rede local"
    )
    assert lines[1] == "Trabalho: abre SSH; liga o mDNS; só aceita ligações de 10.0.0.0/8"
    assert lines[2] == "Público: aceita ligações de qualquer lado"
    assert " de a " not in " ".join(lines) and " de da " not in " ".join(lines)
