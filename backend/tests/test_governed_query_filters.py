"""Exact QuerySpec filter semantics stay explicit and complete."""

import pytest

from app.services.governed_query.engine import _filter_matches


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
