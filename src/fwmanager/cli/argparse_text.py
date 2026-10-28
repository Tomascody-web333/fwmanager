"""argparse's own words ("usage:", "options", its error messages) in the user's language.

argparse looks its sentences up through gettext when it prints them, but Python ships no
Portuguese for them. These are the ones a person meets; any that a Python version words
differently simply stays in English.
"""

from __future__ import annotations

import argparse

from fwmanager.i18n import N_, _

SENTENCES = (
    N_("usage: "),
    N_("options"),
    N_("positional arguments"),
    N_("show this help message and exit"),
    N_("the following arguments are required: %s"),
    N_("invalid choice: %(value)r (choose from %(choices)s)"),
    N_("unrecognized arguments: %s"),
    N_("argument %(argument_name)s: %(message)s"),
    N_("expected one argument"),
    N_("%(prog)s: error: %(message)s\n"),
)


def install() -> None:
    argparse._ = _  # type: ignore[attr-defined]
