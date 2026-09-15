"""Direct-execute tool targets (no StagedChange / bless).

These run immediately via :func:`app.agentive.tooling.dispatch._dispatch_direct`.
Identity is always the dispatch ``principal_id``; args carry data only.
"""

from __future__ import annotations

from typing import Any, Dict


async def mark_notification_read_for_dispatch(
    user_id: str,
    notification_id: str,
) -> Dict[str, Any]:
    """Mark one notification read for the acting principal."""
    from app.agentive.staging_executors import _call_endpoint
    from app.api.notifications import mark_notification_as_read

    result = await _call_endpoint(
        mark_notification_as_read,
        user_id,
        notification_id=notification_id,
    )
    if isinstance(result, dict) and result.get("error"):
        return result
    return {"ok": True, **(result or {})}
