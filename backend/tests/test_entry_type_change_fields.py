"""Tests for entry custom-field pruning on entry-type change."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.operational_model_entry_fields import (
    restrict_custom_fields_to_entry_type,
)


def _entry_type(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        form_schema={
            "fields": [
                {"key": "status", "type": "select"},
            ],
            "base_fields": {},
            "required_tag_groups": [],
        },
    )


def test_restrict_custom_fields_drops_stale_type_specific_keys():
    post_type = _entry_type("Post")
    runtime_tier = {
        "entry_types": [
            {
                "key": "post",
                "name": "Post",
                "fields": [{"key": "status", "type": "select"}],
            }
        ]
    }
    incoming = {
        "invoice_number": "INV-001",
        "status": "draft",
        "_kanban_order": 1.5,
    }

    out = restrict_custom_fields_to_entry_type(
        incoming,
        entry_type=post_type,
        runtime_tier=runtime_tier,
    )

    assert out == {"status": "draft", "_kanban_order": 1.5}
    assert "invoice_number" not in out


def test_restrict_custom_fields_preserves_qb_invoice_keys():
    qb_type = _entry_type("QB Invoice")
    runtime_tier = {
        "entry_types": [
            {
                "key": "qb_invoice",
                "name": "QB Invoice",
                "fields": [
                    {"key": "invoice_number", "type": "text"},
                    {"key": "amount", "type": "number"},
                ],
            }
        ]
    }
    incoming = {
        "invoice_number": "INV-001",
        "amount": 42,
        "status": "open",
    }

    out = restrict_custom_fields_to_entry_type(
        incoming,
        entry_type=qb_type,
        runtime_tier=runtime_tier,
    )

    assert out == {
        "invoice_number": "INV-001",
        "amount": 42,
        "_kanban_stage": "open",
    }
