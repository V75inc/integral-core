"""Exact QuerySpec filter semantics stay explicit and complete."""

import pytest

from app.api.errors import BadRequestError
from app.services.governed_query.engine import _filter_matches
from app.services.query_filters import normalize_filter_expressions


@pytest.mark.parametrize(
    ("value", "op", "expected", "matches"),
    [
        ("Available", "eq", "Available", True),
        ("Available", "neq", "Maintenance", True),
        ("Available", "in", ["Maintenance", "Available"], True),
        (["urgent", "rental"], "contains", "rental", True),
        (None, "exists", False, True),
        (3, "gte", 2, True),
        (3, "lte", 2, False),
    ],
)
def test_filter_matches_declared_queryspec_semantics(value, op, expected, matches):
    assert _filter_matches(value, op=op, expected=expected) is matches


def test_legacy_dashboard_filter_map_has_explicit_queryspec_equivalent():
    filters = normalize_filter_expressions(
        {"custom_fields.status": ["Available", "Maintenance"]}
    )

    assert [(item.field, item.op, item.value) for item in filters] == [
        ("custom_fields.status", "in", ["Available", "Maintenance"])
    ]


def test_ordered_filter_type_mismatch_is_explicit_not_no_results():
    with pytest.raises(BadRequestError, match="cannot compare"):
        _filter_matches("not-a-number", op="gte", expected=2)
