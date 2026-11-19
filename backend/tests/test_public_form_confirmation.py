"""A public form can opt in to neutral confirmation copy."""

import pytest

from app.api.errors import BadRequestError
from app.services.operational_model_compile import (
    _normalize_entry_type_spec,
    normalize_entry_type_form_schema,
)
from app.services.operational_model_merge import (
    _form_schema_from_entry_type_spec,
    merge_entry_type_schema_from_spec,
)


def test_confirmation_is_optional_and_sparse():
    assert "confirmation" not in normalize_entry_type_form_schema({})
    assert normalize_entry_type_form_schema(
        {
            "confirmation": {
                "title": "  Application received  ",
                "allow_another_response": False,
            }
        }
    )["confirmation"] == {
        "title": "Application received",
        "allow_another_response": False,
    }


def test_confirmation_round_trips_from_manifest_into_materialized_schema():
    spec = _normalize_entry_type_spec(
        {"name": "Application", "confirmation": {"message": "Thanks for applying."}}
    )
    schema = _form_schema_from_entry_type_spec(spec)
    assert schema["confirmation"] == {"message": "Thanks for applying."}
    current = normalize_entry_type_form_schema({"confirmation": {"title": "Old"}})
    result, changed = merge_entry_type_schema_from_spec(current, spec)
    assert changed
    assert result["confirmation"] == schema["confirmation"]


@pytest.mark.parametrize(
    "invalid",
    [{"title": 2}, {"message": "a" * 2001}, {"allow_another_response": "false"}],
)
def test_confirmation_rejects_invalid_values(invalid):
    with pytest.raises(BadRequestError):
        normalize_entry_type_form_schema({"confirmation": invalid})
