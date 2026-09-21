"""Shared, explicit filter semantics for operational entry projections."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from app.schemas.governed_query import FilterExpr

_ENTRY_FIELDS = frozenset(
    {
        "id",
        "title",
        "track_id",
        "type_id",
        "status",
        "created_at",
        "updated_at",
    }
)


def entry_field_value(entry: Any, path: str) -> Any:
    """Read one permitted entry field path without ambiguous fallbacks."""
    if path.startswith("custom_fields."):
        value = _value(entry, "custom_fields") or {}
        for key in path.split(".")[1:]:
            if not isinstance(value, Mapping):
                return None
            value = value.get(key)
        return value
    if path not in _ENTRY_FIELDS:
        raise ValueError(f"unsupported filter field {path!r}")
    return _value(entry, path)


def filter_matches(value: Any, *, op: str, expected: Any) -> bool:
    """Apply the declared QuerySpec comparison vocabulary exactly."""
    if op == "eq":
        return value == expected
    if op == "neq":
        return value != expected
    if op == "in":
        return value in (expected if isinstance(expected, list) else [expected])
    if op == "contains":
        return (
            expected in value
            if isinstance(value, (str, list, tuple, set, dict))
            else False
        )
    if op == "exists":
        return (value is not None) is bool(expected)
    if op == "gte":
        return _ordered_compare(value, expected, operator="gte")
    if op == "lte":
        return _ordered_compare(value, expected, operator="lte")
    raise ValueError(f"unsupported filter operator {op!r}")


def normalize_filter_expressions(filters: Any) -> list[FilterExpr]:
    """Normalize legacy dashboard maps and QuerySpec lists into one contract.

    A legacy mapping remains readable: a scalar means equality and a list means
    membership.  New definitions persist the explicit QuerySpec representation.
    """
    if filters in (None, {}, []):
        return []
    if isinstance(filters, Mapping):
        return [
            FilterExpr(
                field=str(field),
                op="in" if isinstance(value, list) else "eq",
                value=value,
            )
            for field, value in filters.items()
        ]
    if not isinstance(filters, Iterable) or isinstance(filters, (str, bytes)):
        raise ValueError("filters must be a field map or a list of filter expressions")
    return [FilterExpr.model_validate(item) for item in filters]


def entry_matches_filters(entry: Any, filters: Any) -> bool:
    """Return whether an entry satisfies every normalized filter expression."""
    return all(
        filter_matches(
            entry_field_value(entry, expr.field), op=expr.op, expected=expr.value
        )
        for expr in normalize_filter_expressions(filters)
    )


def _value(entry: Any, key: str) -> Any:
    return entry.get(key) if isinstance(entry, Mapping) else getattr(entry, key, None)


def _ordered_compare(value: Any, expected: Any, *, operator: str) -> bool:
    if value is None or expected is None:
        return False
    try:
        return value >= expected if operator == "gte" else value <= expected
    except TypeError as exc:
        raise ValueError(
            f"cannot compare filter values for {operator}: "
            f"{type(value).__name__} and {type(expected).__name__}"
        ) from exc
