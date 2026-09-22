"""Durable relative-date values for saved views and governed filters.

Dates are resolved when a view or dashboard is read, not when it is authored.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def normalize_relative_date(value: Any) -> Any:
    """Accept the two natural scaffold placeholders as typed filter values."""
    if value == "{{now}}":
        return {"relative_date_days": 0}
    if value == "{{now_plus_7}}":
        return {"relative_date_days": 7}
    if isinstance(value, list):
        return [normalize_relative_date(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize_relative_date(item) for key, item in value.items()}
    return value


def resolve_relative_date(value: Any, *, now: datetime | None = None) -> Any:
    """Resolve an explicit UTC-day offset, leaving ordinary values untouched."""
    if not isinstance(value, dict) or set(value) != {"relative_date_days"}:
        return value
    days = value["relative_date_days"]
    if type(days) is not int or not -365 <= days <= 365:
        raise ValueError("relative_date_days must be an integer from -365 to 365")
    today = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()
    return (today + timedelta(days=days)).isoformat()
