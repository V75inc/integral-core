"""Fixed membership for a follow-up over a prior query result (W3.6).

The candidate ids stay the prior page. Values are read again. An expired
id is an error, not a fresh scan.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def schema_drift(member_schema: List[Dict[str, Any]], live: List[Any]) -> bool:
    """True when a still-readable member no longer matches its stored schema."""
    by_id = {str(getattr(item, "id", "")): item for item in live}
    for row in member_schema:
        item = by_id.get(str(row.get("id") or ""))
        if item is None:
            continue
        if str(getattr(item, "track_id", "") or "") != str(row.get("track_id") or ""):
            return True
        if int(getattr(item, "schema_revision", 0) or 0) != int(
            row.get("schema_revision") or 0
        ):
            return True
    return False


async def classify_missing(ids: List[str], getter) -> Dict[str, List[str]]:
    """Split ids the caller cannot read into deleted and still-present."""
    absent: List[str] = []
    excluded: List[str] = []
    for item_id in ids:
        node = await getter(item_id)
        if node is None:
            absent.append(item_id)
        else:
            excluded.append(item_id)
    return {"absent_ids": absent, "excluded_ids": excluded}


async def open_result_set(
    result_set_id: str,
    *,
    principal_id: str,
    workspace_id: str,
    query_class: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Load one result set the caller may narrow. Does not read entry values."""
    from app.models.query_result_set import QueryResultSet

    token = (result_set_id or "").strip()
    if not token:
        return {"error": "result_set_required", "detail": "result_set_id is empty"}
    found = list(await QueryResultSet.find({"context.result_set_id": token}))
    if not found:
        return {"error": "not_found", "detail": "no result set with that id"}
    record = found[0]
    if record.principal_id != principal_id:
        return {
            "error": "wrong_principal",
            "detail": "this result set belongs to another principal",
        }
    if record.workspace_id != workspace_id:
        return {
            "error": "wrong_workspace",
            "detail": "this result set belongs to another workspace",
        }
    moment = now or datetime.now(timezone.utc)
    try:
        expires_at = datetime.fromisoformat(record.expires_at.replace("Z", "+00:00"))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        expired = expires_at <= moment
    except (AttributeError, TypeError, ValueError):
        expired = True
    if expired:
        return {
            "error": "expired",
            "detail": "the result set expired and was not rerun",
        }
    if record.query_class and record.query_class != query_class:
        return {
            "error": "incompatible_query",
            "detail": f"result set class is {record.query_class}",
        }
    if not record.member_ids:
        return {
            "error": "membership_unavailable",
            "detail": "this result set has no stored membership",
        }
    return {
        "result_set_id": record.result_set_id,
        "member_ids": list(record.member_ids),
        "member_schema": list(record.member_schema or []),
        "membership_at": record.created_at,
        "query_class": record.query_class or query_class,
    }
