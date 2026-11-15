"""A field can lock itself while a condition on the record holds (readonly_if)."""

import pytest

from app.api.errors import BadRequestError
from app.services.operational_model_compile import _normalize_field_spec

pytestmark = pytest.mark.smoke


def _field(**extra):
    return {"key": "period_start", "name": "Period start", "type": "date", **extra}


def test_readonly_if_not_in_is_carried_through():
    out = _normalize_field_spec(
        _field(readonly_if={"field": "status", "not_in": ["draft"]})
    )
    assert out["readonly_if"] == {"field": "status", "not_in": ["draft"]}
    assert out["readonly"] is False  # not locked outright


def test_readonly_if_equals_and_in_are_carried_through():
    eq = _normalize_field_spec(
        _field(readonly_if={"field": "status", "equals": "paid"})
    )
    assert eq["readonly_if"] == {"field": "status", "equals": "paid"}
    within = _normalize_field_spec(
        _field(readonly_if={"field": "status", "in": ["approved", "paid"]})
    )
    assert within["readonly_if"] == {"field": "status", "in": ["approved", "paid"]}


def test_a_field_without_it_has_no_key():
    assert "readonly_if" not in _normalize_field_spec(_field())


@pytest.mark.parametrize(
    "bad",
    [
        {"equals": "x"},  # no field
        {"field": "status"},  # no mode
        {"field": "status", "equals": "a", "in": ["a"]},  # two modes
        {"field": "status", "in": "draft"},  # in must be a list
        {"field": "status", "not_in": "draft"},
        "draft",
    ],
)
def test_a_malformed_condition_is_refused(bad):
    with pytest.raises(BadRequestError):
        _normalize_field_spec(_field(readonly_if=bad))
