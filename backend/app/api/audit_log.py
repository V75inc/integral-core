"""Audit log query endpoint (PROV-04).

Returns ChangeEvents the caller is permitted to see, ordered (ts, id) DESC.
New confidential continuation tokens bind the principal, selected workspace,
filters and direction; current permissions are still checked on every read.

Per D-07: every event in the result set passes can_view_track / can_view_space /
identity check before being returned. Cross-tenant queries return empty (no leak).

Storage shape: ChangeEvents live as ``DBLog`` rows with
``log_level="CHANGE_EVENT"`` in the logging database. Reads go through
bounded ``ChangeEventLogger.find_page`` windows and are surfaced via the
``ChangeEventEnvelope`` wire shape. A continuation may advance through withheld
events without revealing their identifiers or promising another visible result.
"""

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import MissingAuthenticationError
from app.api.utils import resolve_principal_id
from app.services.change_event_logger import (
    ChangeEventEnvelope,
    get_change_event_logger,
    is_change_event_enabled,
)
from app.services.pagination import build_paginated_response

logger = logging.getLogger(__name__)


def _envelope_key(env: ChangeEventEnvelope) -> Tuple[str, str]:
    """Sort key for ChangeEventEnvelope: (id, ts) — pagination uses this for stable order."""
    return (env.id or "", env.ts or "")


@endpoint("/audit-log", methods=["GET"], auth=True, tags=["Audit"])
async def list_audit_log(
    request: Request,
    scope: Optional[str] = None,
    actor_kind: Optional[str] = None,
    action: Optional[str] = None,
    resource_id: Optional[str] = None,
    resource_type: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """Return ChangeEvents the user is permitted to see, paginated by (ts, id).

    Query params:
    - scope: "track:<id>" | "app:<id>" | "user:<id>" — narrow to a scope
    - actor_kind: "human" | "agent" | "connector" | "system" — narrow by actor type
    - action: exact ChangeEvent action (e.g. ``policy.deny``, ``entry.create``)
    - resource_id / resource_type: narrow to a resource (F1 forensic filters)
    - cursor: opaque base64 cursor from previous response
    - limit: page size (default 50; capped via AUDIT_LOG_PAGE_SIZE_MAX env, default 200)
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    # Hard cap on limit (page size cap per D-08).
    page_cap = int(os.getenv("AUDIT_LOG_PAGE_SIZE_MAX", "200"))
    if limit > page_cap:
        limit = page_cap
    if limit < 1:
        limit = 1

    from app.services.event_history import read_event_history
    from app.services.request_scope import resolve_execution_scope_from_request

    execution = await resolve_execution_scope_from_request(
        request, user_id, origin="event_history"
    )

    # Kill switch: when ChangeEvents are disabled, the audit log is empty.
    if not is_change_event_enabled():
        empty: List[ChangeEventEnvelope] = []
        _empty_page, empty_response = build_paginated_response(
            empty, cursor, limit, _envelope_key
        )
        empty_response["events"] = []
        return empty_response

    return await read_event_history(
        logger=get_change_event_logger(),
        user_id=user_id,
        workspace_id=execution.workspace_id,
        action="audit_log.read",
        cursor=cursor,
        limit=limit,
        descending=True,
        scope=scope,
        actor_kind=actor_kind,
        event_action=action,
        resource_id=resource_id,
        resource_type=resource_type,
    )
