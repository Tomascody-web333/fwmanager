from fwmanager.errors import ValidationError
from fwmanager.i18n import _, set_language
from fwmanager.messages import describe, describe_error


def teardown_function():
    set_language(None)


def test_untranslated_falls_back_to_english():
    set_language("pt_PT", localedir="/nonexistent")
    assert _("Applied.") == "Applied."


def test_describe_known_and_unknown_codes():
    assert describe("invalid_port") == "Invalid port."
    assert describe("something_new") == "Internal error."
    assert describe_error(ValidationError("x", code="invalid_cidr")).startswith("Invalid network")
