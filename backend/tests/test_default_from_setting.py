"""``default_from_setting`` field knob — compile pass-through."""

from __future__ import annotations

from app.services.operational_model_compile import _normalize_field_spec


def test_normalize_field_passes_default_from_setting():
    out = _normalize_field_spec(
        {
            "key": "currency",
            "name": "Currency",
            "type": "select",
            "required": True,
            "default_from_setting": "default_currency",
            "enum": ["USD", "GYD"],
        }
    )
    assert out["default_from_setting"] == "default_currency"


def test_normalize_field_omits_blank_default_from_setting():
    out = _normalize_field_spec(
        {
            "key": "currency",
            "name": "Currency",
            "type": "text",
            "default_from_setting": "  ",
        }
    )
    assert "default_from_setting" not in out
