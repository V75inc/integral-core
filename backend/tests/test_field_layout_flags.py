"""Field presentation is a reusable compiler contract, independent of Apps."""

import pytest

from app.services.operational_model_compile import _normalize_field_spec


@pytest.mark.parametrize("field_type", ["text", "json", "number", "select"])
def test_field_layout_options_have_neutral_defaults(field_type):
    field = _normalize_field_spec({"key": "value", "type": field_type})
    for key in ("hide_on_card", "full_width", "multiline", "hide_when_empty"):
        assert field[key] is False
    assert field["max_lines"] == 0


def test_field_layout_options_survive_compilation():
    options = {
        "hide_on_card": True,
        "full_width": True,
        "max_lines": 4,
        "multiline": True,
        "hide_when_empty": True,
    }
    field = _normalize_field_spec({"key": "value", "type": "text", **options})
    assert {key: field[key] for key in options} == options
