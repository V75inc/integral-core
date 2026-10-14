"""Document template resolution — default pick + document_type-only generate."""

from __future__ import annotations

from app.services.documents.entry_template_store import (
    TemplateView,
    _pick_default_active_template,
)


def test_pick_default_active_template_prefers_is_default() -> None:
    rows = [
        TemplateView(
            id="a",
            name="A",
            module="finance",
            document_type="invoice",
            context_type="",
            is_default=False,
        ),
        TemplateView(
            id="b",
            name="B",
            module="human-resources",
            document_type="employment_contract",
            context_type="",
            is_default=True,
        ),
    ]
    picked = _pick_default_active_template(rows)
    assert picked is not None
    assert picked.id == "b"


def test_pick_default_active_template_falls_back_to_first() -> None:
    rows = [
        TemplateView(
            id="only",
            name="Only",
            module="payroll",
            document_type="payslip",
            context_type="",
        ),
    ]
    assert _pick_default_active_template(rows).id == "only"
    assert _pick_default_active_template([]) is None
