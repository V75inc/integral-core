"""Labels on generated-document issuance log entries."""

from types import SimpleNamespace

from app.services.documents.entry_template_store import _context_entry_display_label


def test_context_entry_display_label_prefers_employee_name():
    entry = SimpleNamespace(
        title="Onboarding form",
        custom_fields={"employee_name": "Jane Doe"},
    )
    assert _context_entry_display_label(entry) == "Jane Doe"


def test_context_entry_display_label_falls_back_to_title():
    entry = SimpleNamespace(title="Alex Smith", custom_fields={})
    assert _context_entry_display_label(entry) == "Alex Smith"
