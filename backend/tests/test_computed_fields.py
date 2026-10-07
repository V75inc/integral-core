"""Phase 1 computed fields: compile, drop on write, project on read."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.exceptions import BadRequestError
from app.services.computed_fields import (
    assert_stored_query_field,
    attach_computed_expression,
    project_computed_values,
)
from app.services.operational_model_compile import normalize_entry_type_form_schema
from app.services.operational_model_entry_fields import (
    validate_and_materialize_entry_custom_fields,
)

_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "frontend/src/components/entries/__tests__/computed_expression_cases.json"
)
_CASES = json.loads(_FIXTURE.read_text())


def _calculator_schema():
    """Compile the retail markup calculator entry type."""
    fields = []
    for field in _CASES["calculator"]["fields"]:
        spec = {
            "key": field["key"],
            "name": field["name"],
            "type": field["type"],
        }
        if field["type"] == "computed":
            spec["expression"] = field["expression"]["source"]
        fields.append(spec)
    return normalize_entry_type_form_schema({"fields": fields})


def test_shared_cases_parse_and_evaluate():
    """The shared fixture's source, AST, and result stay in lockstep."""
    for case in _CASES["cases"]:
        compiled = attach_computed_expression(case["name"], case["source"])
        assert compiled["ast"] == case["ast"]
        assert (
            project_computed_values(
                case["inputs"],
                [
                    {
                        "key": "result",
                        "type": "computed",
                        "expression": compiled,
                    }
                ],
            )["result"]
            == case["result"]
        )


def test_retail_calculator_projects_derived_amounts():
    """Cost, markup, and discount produce the five derived prices."""
    schema = _calculator_schema()
    projected = project_computed_values(
        {
            **_CASES["calculator"]["inputs"],
            "markup_amount": 999,
        },
        schema["fields"],
    )
    for key, expected in _CASES["calculator"]["results"].items():
        assert projected[key] == expected
    stored_keys = {
        field["key"] for field in schema["fields"] if field["type"] == "computed"
    }
    assert stored_keys <= set(projected)


def test_number_field_with_expression_is_stored_as_computed():
    """The resident's shape, type number plus expression, becomes computed."""
    schema = normalize_entry_type_form_schema(
        {
            "fields": [
                {"key": "cost_price", "name": "Cost Price", "type": "number"},
                {
                    "key": "markup_percentage",
                    "name": "Markup Percentage",
                    "type": "number",
                },
                {
                    "key": "markup_amount",
                    "name": "Markup Amount",
                    "type": "number",
                    "computed": True,
                    "expression": "cost_price * markup_percentage / 100",
                },
            ]
        }
    )
    derived = schema["fields"][2]
    assert derived["type"] == "computed"
    assert derived["readonly"] is True
    assert derived["expression"]["source"] == "cost_price * markup_percentage / 100"
    projected = project_computed_values(
        {"cost_price": 80, "markup_percentage": 25, "markup_amount": 1},
        schema["fields"],
    )
    assert projected["markup_amount"] == 20


def test_computed_field_requires_an_expression():
    """A computed field with no expression is rejected by name."""
    with pytest.raises(BadRequestError, match="markup_amount.*requires an expression"):
        normalize_entry_type_form_schema(
            {
                "fields": [
                    {"key": "cost_price", "type": "number"},
                    {"key": "markup_amount", "type": "computed"},
                ]
            }
        )


def test_computed_field_rejects_unknown_calls_and_fields():
    """Python and unknown fields fail compilation."""
    with pytest.raises(BadRequestError, match="invalid expression"):
        normalize_entry_type_form_schema(
            {
                "fields": [
                    {
                        "key": "markup_amount",
                        "type": "computed",
                        "expression": "len(cost_price)",
                    }
                ]
            }
        )
    with pytest.raises(BadRequestError, match="unknown field 'sku'"):
        normalize_entry_type_form_schema(
            {
                "fields": [
                    {
                        "key": "markup_amount",
                        "type": "computed",
                        "expression": "sku * 2",
                    }
                ]
            }
        )


def test_computed_field_rejects_cycles_text_arithmetic_and_required():
    """Cycles, mixed types, and required computed fields fail by name."""
    with pytest.raises(BadRequestError, match="circular expression"):
        normalize_entry_type_form_schema(
            {
                "fields": [
                    {
                        "key": "left",
                        "type": "computed",
                        "expression": "right + 1",
                    },
                    {
                        "key": "right",
                        "type": "computed",
                        "expression": "left + 1",
                    },
                ]
            }
        )
    with pytest.raises(BadRequestError, match="cannot use text field 'label'"):
        normalize_entry_type_form_schema(
            {
                "fields": [
                    {"key": "label", "type": "text"},
                    {
                        "key": "total",
                        "type": "computed",
                        "expression": "label + 1",
                    },
                ]
            }
        )
    with pytest.raises(BadRequestError, match="cannot be required"):
        normalize_entry_type_form_schema(
            {
                "fields": [
                    {"key": "cost_price", "type": "number"},
                    {
                        "key": "doubled",
                        "type": "computed",
                        "required": True,
                        "expression": "cost_price * 2",
                    },
                ]
            }
        )


def test_filter_on_computed_field_is_rejected():
    """A query cannot filter or sort a computed field."""
    fields = _calculator_schema()["fields"]
    with pytest.raises(BadRequestError, match="Cannot filter or sort computed field"):
        assert_stored_query_field("custom_fields.selling_price", fields)
    with pytest.raises(BadRequestError, match="selling_price"):
        assert_stored_query_field("-custom_fields.selling_price", fields)
    assert_stored_query_field("custom_fields.cost_price", fields)


@pytest.mark.asyncio
async def test_write_stores_inputs_and_drops_derived_keys():
    """A write that echoes derived amounts keeps only the three inputs."""
    schema = _calculator_schema()
    stored, _refs = await validate_and_materialize_entry_custom_fields(
        track=SimpleNamespace(id="track"),
        entry_type=SimpleNamespace(name="Calculator"),
        custom_fields={
            "cost_price": 100,
            "markup_percentage": 50,
            "discount_percentage": 10,
            "markup_amount": 999,
            "selling_price": 1,
        },
        runtime_tier={
            "entry_types": [
                {
                    "key": "calculator",
                    "name": "Calculator",
                    "fields": schema["fields"],
                }
            ]
        },
    )
    assert stored["cost_price"] == 100
    assert stored["markup_percentage"] == 50
    assert stored["discount_percentage"] == 10
    assert "markup_amount" not in stored
    assert "selling_price" not in stored
    assert "profit_amount" not in stored
