"""Shared, explicit filter semantics for operational entry projections."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from app.schemas.governed_query import FilterExpr
from app.services.relative_date_filters import resolve_relative_date

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

# One comparison vocabulary for saved views, query_entries, counts, and
# dashboards. Symbolic spellings fold in at the boundary; storage uses these.
FILTER_OPS = frozenset(
    {"eq", "neq", "in", "not_in", "contains", "gt", "lt", "gte", "lte", "exists"}
)
_OP_ALIASES = {
    "=": "eq",
    "==": "eq",
    "!=": "neq",
    "ne": "neq",
    ">": "gt",
    "<": "lt",
    ">=": "gte",
    "<=": "lte",
    "nin": "not_in",
    "$nin": "not_in",
    "$in": "in",
}


def canonical_filter_op(raw: Any) -> str:
    """Fold ``operator`` spellings and symbols onto the stored op."""
    text = str("eq" if raw is None else raw).strip() or "eq"
    return _OP_ALIASES.get(text, text)


def canonical_filter(item: Mapping[str, Any]) -> dict[str, Any]:
    """Return ``{field, op, value}``. ``op`` wins when both keys are set."""
    if item.get("op") not in (None, ""):
        raw_op = item.get("op")
    else:
        raw_op = item.get("operator", "eq")
    op = canonical_filter_op(raw_op)
    if op not in FILTER_OPS:
        raise ValueError(f"unsupported filter operator {op!r}")
    field = str(item.get("field") or "").strip()
    if not field:
        raise ValueError("filter field is required")
    value = item.get("value")
    if op in {"in", "not_in"} and not isinstance(value, list):
        raise ValueError(f"{op} filter value must be a list")
    return {"field": field, "op": op, "value": value}


def entry_field_value(entry: Any, path: str) -> Any:
    """Read one permitted entry field path without ambiguous fallbacks."""
    if path.startswith("custom_fields."):
        value = _value(entry, "custom_fields") or {}
        for key in path.split(".")[1:]:
            if not isinstance(value, Mapping):
                return None
            if key in value:
                value = value[key]
                continue
            # Agents receive field labels in the UI ("Priority") while the
            # Operational Model persists stable keys ("priority"). Treat a
            # unique case-insensitive custom-field match as the same field,
            # but never guess when a model declares ambiguous keys.
            candidates = [
                actual_key
                for actual_key in value
                if isinstance(actual_key, str)
                and actual_key.casefold() == key.casefold()
            ]
            if len(candidates) != 1:
                return None
            value = value[candidates[0]]
        return value
    if path not in _ENTRY_FIELDS:
        raise ValueError(f"unsupported filter field {path!r}")
    return _value(entry, path)


def filter_matches(value: Any, *, op: str, expected: Any) -> bool:
    """Apply the declared QuerySpec comparison vocabulary exactly."""
    expected = resolve_relative_date(expected)
    if op == "eq":
        return value == expected
    if op == "neq":
        return value != expected
    if op == "in":
        return value in (expected if isinstance(expected, list) else [expected])
    if op == "not_in":
        return value not in (expected if isinstance(expected, list) else [expected])
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
    if op == "gt":
        return _ordered_compare(value, expected, operator="gt")
    if op == "lt":
        return _ordered_compare(value, expected, operator="lt")
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
    normalized: list[FilterExpr] = []
    for item in filters:
        if isinstance(item, Mapping):
            item = canonical_filter(item)
        normalized.append(FilterExpr.model_validate(item))
    return normalized


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
        return {
            "gte": lambda: value >= expected,
            "lte": lambda: value <= expected,
            "gt": lambda: value > expected,
            "lt": lambda: value < expected,
        }[operator]()
    except TypeError as exc:
        raise ValueError(
            f"cannot compare filter values for {operator}: "
            f"{type(value).__name__} and {type(expected).__name__}"
        ) from exc
