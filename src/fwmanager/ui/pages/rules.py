"""Rules: sentences that adjust the profiles, with a preview before anything is saved."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from fwmanager.i18n import _  # noqa: E402
from fwmanager.ui import rule_text  # noqa: E402
from fwmanager.ui.common import (  # noqa: E402
    button,
    clear,
    label_for_screen_readers,
    toast,
    toast_error,
)
from fwmanager.ui.dialogs import RuleEditor  # noqa: E402


class RulesPage(Adw.PreferencesPage):
    def __init__(self, controller, on_changed) -> None:
        super().__init__(title=_("Rules"), icon_name="view-list-bullet-symbolic")
        self._c, self._on_changed = controller, on_changed
        self._rows: list[Adw.ActionRow] = []
        self._profiles: dict[str, dict] = {}
        self._titles: dict[str, str] = {}

        self.group = Adw.PreferencesGroup(
            title=_("Your rules"),
            description=_(
                "Rules adjust what the profiles allow. Nothing changes on your computer "
                "until you apply a profile."
            ),
        )
        self.add(self.group)
        # Below the list, not beside the explanation: on narrow windows and with large text the
        # header has no room for both.
        self.add_btn = button(_("Add a rule"), suggested=True, icon="list-add-symbolic")
        self.add_btn.set_halign(Gtk.Align.CENTER)
        self.add_btn.add_css_class("pill")
        self.add_btn.connect("clicked", lambda *_: self._open_editor())
        actions = Adw.PreferencesGroup()
        actions.add(self.add_btn)
        self.add(actions)

    def load(self) -> None:
        self._c.load_profiles(self._profiles_loaded)

    def _profiles_loaded(self, result, error) -> None:
        if error is not None:
            toast_error(self, error)
            return
        self._profiles, self._titles = result
        self._c.rules(self._rules_loaded)

    def _rules_loaded(self, result, error) -> None:
        if error is not None:
            toast_error(self, error)
            return
        clear(self.group, self._rows)
        for rule_id, template, params in result:
            row = Adw.ActionRow(
                title=rule_text.sentence(template, params, self._titles),
                subtitle=rule_text.template_title(template),
                title_lines=3,
            )
            remove = button("", flat=True, icon="user-trash-symbolic")
            remove.set_tooltip_text(_("Remove this rule"))
            label_for_screen_readers(remove, _("Remove this rule"))
            remove.connect(
                "clicked", lambda _b, rid=rule_id: self._c.remove_rule(rid, self._removed)
            )
            row.add_suffix(remove)
            self.group.add(row)
            self._rows.append(row)
        if not self._rows:
            row = Adw.ActionRow(
                title=_("No rules yet."),
                subtitle=_("Press “Add a rule” to start from a ready-made sentence."),
            )
            self.group.add(row)
            self._rows.append(row)

    def _open_editor(self) -> None:
        if not self._profiles:
            self.load()
            return
        RuleEditor(self._c, list(self._profiles), self._titles, self._saved).present(
            self.get_root()
        )

    def _saved(self) -> None:
        toast(self, _("Rule saved. Apply a profile to use it."))
        self.load()
        self._on_changed()

    def _removed(self, _result, error) -> None:
        if error is not None:
            toast_error(self, error)
            return
        toast(self, _("Rule removed."))
        self.load()
        self._on_changed()
