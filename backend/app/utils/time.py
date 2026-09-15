"""UTC datetime helpers — single canonical now()/isoformat() for app/agentive/.

Standardizes the agentive layer on tz-aware datetimes per D-05 and the
"Two parallel datetime conventions inside agentive" concern in CONCERNS.md.
Use these helpers anywhere the agentive code needs the current UTC time.
"""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return the current UTC time as a tz-aware datetime."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (with `+00:00` suffix)."""
    return datetime.now(timezone.utc).isoformat()
