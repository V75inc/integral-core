"""ui_contributions compile: iframe XOR native Core region shapes."""

from __future__ import annotations

import pytest

from app.exceptions import BadRequestError
from app.services.operational_model_compile import _normalize_ui_contributions


def test_extension_view_contribution():
    out = _normalize_ui_contributions(
        [
            {
                "placement": "entry_compose",
                "extension_view_key": "document_lines",
                "layout": "wide",
            }
        ],
        where="test",
    )
    assert out == [
        {
            "placement": "entry_compose",
            "extension_view_key": "document_lines",
            "layout": "wide",
        }
    ]


def test_native_view_contribution():
    out = _normalize_ui_contributions(
        [
            {
                "placement": "entry_detail",
                "view": "invoice_lines_editor",
                "layout": "wide",
            }
        ],
        where="test",
    )
    assert out[0]["view"] == "invoice_lines_editor"
    assert "extension_view_key" not in out[0]


def test_native_view_type_with_config():
    out = _normalize_ui_contributions(
        [
            {
                "placement": "entry_compose",
                "view_type": "editable_related_lines",
                "config": {"relation": "invoice"},
            }
        ],
        where="test",
    )
    assert out[0]["view_type"] == "editable_related_lines"
    assert out[0]["config"]["relation"] == "invoice"


def test_rejects_both_extension_and_native():
    with pytest.raises(BadRequestError, match="not both"):
        _normalize_ui_contributions(
            [
                {
                    "placement": "entry_compose",
                    "extension_view_key": "document_lines",
                    "view": "invoice_lines_editor",
                }
            ],
            where="test",
        )


def test_rejects_neither():
    with pytest.raises(BadRequestError, match="requires extension_view_key or"):
        _normalize_ui_contributions(
            [{"placement": "entry_compose"}],
            where="test",
        )


def test_owns_form_flag():
    out = _normalize_ui_contributions(
        [
            {
                "placement": "entry_compose",
                "view": "invoice_document",
                "layout": "wide",
                "owns_form": True,
            }
        ],
        where="test",
    )
    assert out[0]["owns_form"] is True


def test_title_from_fields():
    out = _normalize_ui_contributions(
        [
            {
                "placement": "entry_compose",
                "view": "doc_shell",
                "owns_form": True,
                "title_from_fields": ["doc_number", "party_name"],
            }
        ],
        where="test",
    )
    assert out[0]["title_from_fields"] == ["doc_number", "party_name"]
