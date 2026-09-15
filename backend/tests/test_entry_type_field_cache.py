"""Tests for per-type custom field preservation on entry type changes."""

from types import SimpleNamespace

from app.services.content_profile_entry_fields import (
    transition_custom_fields_on_type_change,
)


def _entry_type(name: str, field_keys: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        form_schema={
            "fields": [{"key": k, "name": k, "type": "text"} for k in field_keys],
            "base_fields": {},
        },
    )


def test_transition_archives_and_restores_round_trip():
    runtime_tier: dict = {"entry_types": []}
    invoice_type = _entry_type("QB_invoice", ["invoice_number", "amount"])
    post_type = _entry_type("Post", [])

    original = {
        "invoice_number": "INV-1",
        "amount": 42,
        "_kanban_order": 1,
    }

    after_post = transition_custom_fields_on_type_change(
        original,
        old_entry_type=invoice_type,
        new_entry_type=post_type,
        runtime_tier=runtime_tier,
    )
    assert "invoice_number" not in after_post
    assert "amount" not in after_post
    assert after_post["_kanban_order"] == 1
    cache = after_post["_type_field_cache"]
    assert cache["qb_invoice"]["invoice_number"] == "INV-1"
    assert cache["qb_invoice"]["amount"] == 42

    after_invoice = transition_custom_fields_on_type_change(
        after_post,
        old_entry_type=post_type,
        new_entry_type=invoice_type,
        runtime_tier=runtime_tier,
    )
    assert after_invoice["invoice_number"] == "INV-1"
    assert after_invoice["amount"] == 42
