"""Governed aggregates over open-class entries (W3.1).

One pass, exact totals. Duplicate rows of the same entry count once.
A scan past ``budget`` is refused before any total is returned.
Installed-App tracks stay out through the query boundary.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.schemas.entry_aggregate import AggregateSpec

_PLATFORM = ("status", "track_id", "type_id", "title", "created_at", "updated_at")
_VALUE_OPS = frozenset({"sum", "avg", "min", "max"})


def _refused(code: str, detail: str, **extra: Any) -> Dict[str, Any]:
    body = {"error": code, "detail": detail}
    body.update(extra)
    return body


def _entry_id(row: Dict[str, Any]) -> str:
    return str(row.get("id") or "")


def _raw_field(row: Dict[str, Any], field: str) -> Any:
    if field in _PLATFORM:
        return row.get(field)
    fields = (
        row.get("custom_fields") if isinstance(row.get("custom_fields"), dict) else {}
    )
    return fields.get(field)


def _money(value: Any) -> Optional[tuple]:
    if not isinstance(value, dict):
        return None
    amount = value.get("amount", value.get("value"))
    currency = value.get("currency") or value.get("unit")
    if amount is None or not currency:
        return None
    return amount, str(currency)


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise InvalidOperation
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str) and value.strip():
        return Decimal(value.strip())
    raise InvalidOperation


def _bucket(value: Any, tz: ZoneInfo) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    if len(text) == 10 and text[4] == "-" and "T" not in text:
        return text
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(tz).date().isoformat()


def _group_keys(value: Any) -> List[str]:
    if isinstance(value, list):
        keys = []
        for item in value:
            if item is None or item == "":
                continue
            keys.append(str(item))
        return list(dict.fromkeys(keys))
    if value is None or value == "":
        return []
    return [str(value)]


def _display(number: Optional[Decimal], scale: Optional[int]) -> Optional[str]:
    if number is None:
        return None
    if scale is None:
        return format(number, "f")
    quantum = Decimal("1").scaleb(-scale)
    return format(number.quantize(quantum), "f")


def _dedupe(rows: List[Dict[str, Any]]) -> Dict[str, Any] | List[Dict[str, Any]]:
    seen: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ident = _entry_id(row) or f"row-{len(seen)}"
        prev = seen.get(ident)
        if prev is None:
            seen[ident] = row
            continue
        if prev != row:
            return _refused(
                "duplicate_conflict",
                f"entry {ident} arrived twice with different values",
            )
    return list(seen.values())


def aggregate_rows(rows: List[Dict[str, Any]], spec: AggregateSpec) -> Dict[str, Any]:
    """Compute one aggregate. Rows are already the authorized set."""
    try:
        tz = ZoneInfo(spec.timezone or "UTC")
    except ZoneInfoNotFoundError:
        return _refused("invalid_timezone", f"unknown timezone {spec.timezone!r}")
    op = spec.op
    field = (spec.field or "").strip()
    if op in _VALUE_OPS | {"distinct"} and not field:
        return _refused("field_required", f"{op} requires a field")
    deduped = _dedupe(rows)
    if isinstance(deduped, dict):
        return deduped
    if len(deduped) > spec.budget:
        return _refused(
            "over_budget",
            f"{len(deduped)} entries exceeds the aggregate budget of {spec.budget}",
            total=len(deduped),
            budget=spec.budget,
        )

    group_by = (spec.group_by or "").strip()
    date_field = ""
    if group_by == "date":
        date_field = "created_at"
    elif group_by.startswith("date:"):
        date_field = group_by.split(":", 1)[1].strip()

    measures: List[tuple] = []
    nulls = 0
    currency: Optional[str] = None
    saw_plain = False
    for row in deduped:
        ident = _entry_id(row)
        if date_field:
            keys = _group_keys(_bucket(_raw_field(row, date_field), tz))
        elif group_by:
            keys = _group_keys(_raw_field(row, group_by))
        else:
            keys = [""]
        if op == "count":
            if group_by and not keys:
                continue
            measures.append((ident, keys, Decimal(1), None))
            continue
        raw = _raw_field(row, field)
        if raw is None or raw == "":
            nulls += 1
            continue
        money = _money(raw)
        numeric = money[0] if money else raw
        unit = money[1] if money else None
        if op in _VALUE_OPS:
            try:
                number = _decimal(numeric)
            except InvalidOperation:
                return _refused(
                    "invalid_number",
                    f"entry {ident or field} has a non-numeric {field}",
                )
            if unit:
                if saw_plain or (currency and unit != currency):
                    return _refused(
                        "incompatible_currency",
                        f"{currency or 'a plain number'} and {unit} cannot be combined",
                    )
                currency = unit
            else:
                if currency:
                    return _refused(
                        "incompatible_currency",
                        "plain numbers and currency amounts cannot be combined",
                    )
                saw_plain = True
            if group_by and not keys:
                continue
            measures.append((ident, keys or [""], number, unit))
        else:
            token = numeric if not isinstance(numeric, (dict, list)) else str(numeric)
            if isinstance(token, bool):
                return _refused(
                    "invalid_number", f"entry {ident} has a boolean {field}"
                )
            if isinstance(token, (int, float, Decimal)):
                try:
                    token = _decimal(token).normalize()
                except InvalidOperation:
                    return _refused(
                        "invalid_number", f"entry {ident} has a bad {field}"
                    )
            if group_by and not keys:
                continue
            measures.append((ident, keys or [""], token, None))

    grouped: Dict[str, List[Any]] = {}
    for _ident, keys, measure, _unit in measures:
        for key in keys:
            grouped.setdefault(key, []).append(measure)

    def _reduce(values: List[Any]) -> Any:
        if op == "count":
            return len(values)
        if op == "distinct":
            return len(set(values))
        if not values:
            return Decimal(0) if op == "sum" else None
        numbers = [value for value in values if isinstance(value, Decimal)]
        if op == "sum":
            return sum(numbers, Decimal(0))
        if op == "avg":
            return sum(numbers, Decimal(0)) / Decimal(len(numbers))
        if op == "min":
            return min(numbers)
        return max(numbers)

    whole = [measure for _ident, _keys, measure, _unit in measures]
    if op == "count":
        value: Any = len(deduped) if not group_by else len(measures)
    elif op == "distinct":
        value = _reduce(whole)
    elif not whole:
        value = Decimal(0) if op == "sum" else None
    else:
        value = _reduce(whole)

    def _emit(number: Any) -> Any:
        if op in ("count", "distinct"):
            return int(number or 0)
        if number is None:
            return None
        return format(number, "f")

    groups = []
    if group_by:
        for key in sorted(grouped):
            reduced = _reduce(grouped[key])
            groups.append(
                {
                    "key": key,
                    "value": _emit(reduced),
                    "display": (
                        _display(reduced, spec.scale)
                        if isinstance(reduced, Decimal)
                        else None
                    ),
                    "count": len(grouped[key]),
                }
            )
    return {
        "op": op,
        "field": field or None,
        "group_by": group_by or None,
        "value": _emit(value),
        "display": _display(value, spec.scale) if isinstance(value, Decimal) else None,
        "currency": currency,
        "count": len(deduped),
        "nulls_ignored": nulls,
        "groups": groups,
        "complete": True,
    }


async def aggregate_entries(
    user_id: str,
    op: str,
    field: str = "",
    group_by: str = "",
    track_id: str = "",
    timezone: str = "UTC",
    since: str = "",
    until: str = "",
    budget: int = 5000,
    scale: Optional[int] = None,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Aggregate open-class entries the caller can read. Writes nothing."""
    try:
        spec = AggregateSpec(
            op=op,  # type: ignore[arg-type]
            field=field or "",
            group_by=group_by or "",
            timezone=timezone or "UTC",
            scale=scale,
            budget=budget,
        )
    except Exception as exc:  # noqa: BLE001 — bad op/budget is a refusal
        return _refused("invalid_argument", str(exc))

    from app.services.agent_insights import query_all_entries

    queried = await query_all_entries(
        user_id=user_id,
        track_id=track_id or None,
        since=since or None,
        until=until or None,
        workspace_id=workspace_id,
    )
    if queried.get("refused"):
        return {
            "error": "refused",
            "detail": "this track is outside the open-class aggregate",
            "refused": queried["refused"],
        }
    if not queried.get("complete", True):
        return _refused("incomplete", "the entry scan did not return every match")
    result = aggregate_rows(list(queried.get("entries") or []), spec)
    if queried.get("boundary"):
        result["boundary"] = queried["boundary"]
    return result
