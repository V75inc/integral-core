"""Type-aware formatters for document template field values."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional


def format_text(value: Any, fmt: Optional[str] = None) -> str:
    text = "" if value is None else str(value)
    mode = (fmt or "original").lower()
    if mode in ("upper", "uppercase"):
        return text.upper()
    if mode in ("lower", "lowercase"):
        return text.lower()
    if mode in ("title", "titlecase", "title_case"):
        return text.title()
    return text


def _parse_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    # ISO date or datetime
    try:
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def format_date(value: Any, fmt: Optional[str] = None) -> str:
    d = _parse_date(value)
    if d is None:
        return "" if value is None else str(value)
    mode = (fmt or "long").lower()
    if mode in ("iso", "yyyy-mm-dd"):
        return d.isoformat()
    if mode in ("short", "dd/mm/yyyy"):
        return d.strftime("%d/%m/%Y")
    if mode in ("us", "mm/dd/yyyy"):
        return d.strftime("%m/%d/%Y")
    if mode in ("long_uk", "d month yyyy"):
        return d.strftime("%-d %B %Y") if hasattr(d, "strftime") else d.strftime("%d %B %Y")
    # default long: August 29, 2026
    try:
        return d.strftime("%B %-d, %Y")
    except ValueError:
        return d.strftime("%B %d, %Y").replace(" 0", " ")


def format_currency(value: Any, fmt: Optional[str] = None, currency: str = "GYD") -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "" if value is None else str(value)
    mode = (fmt or "code_prefix").lower()
    # Simple thousands grouping without locale dependency.
    whole = int(round(amount))
    grouped = f"{whole:,}"
    if mode in ("plain", "number"):
        return grouped
    if mode in ("symbol", "$"):
        return f"${grouped}"
    if mode in ("code_suffix",):
        return f"{grouped} {currency}"
    return f"{currency} {grouped}"


def format_boolean(value: Any, fmt: Optional[str] = None) -> str:
    truthy = bool(value)
    mode = (fmt or "yes_no").lower()
    if mode in ("true_false",):
        return "true" if truthy else "false"
    if mode in ("yn", "y_n"):
        return "Y" if truthy else "N"
    return "Yes" if truthy else "No"


def format_value(
    value: Any,
    *,
    data_type: str = "text",
    fmt: Optional[str] = None,
    currency: str = "GYD",
) -> str:
    dt = (data_type or "text").lower()
    if dt in ("date", "datetime"):
        return format_date(value, fmt)
    if dt in ("currency", "money", "number_currency"):
        return format_currency(value, fmt, currency=currency)
    if dt in ("boolean", "bool"):
        return format_boolean(value, fmt)
    if dt in ("number", "integer", "float"):
        try:
            n = float(value)
            if n.is_integer():
                return str(int(n))
            return str(n)
        except (TypeError, ValueError):
            return "" if value is None else str(value)
    return format_text(value, fmt)
