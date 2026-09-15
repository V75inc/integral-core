"""Audit log query endpoint (PROV-04).

Returns ChangeEvents the caller is permitted to see, ordered (ts, id) DESC,
paginated by opaque base64 cursor (D-08 — direct reuse of pagination helper).

Per D-07: every event in the result set passes can_view_track / can_view_space /
identity check before being returned. Cross-tenant queries return empty (no leak).

Storage shape: ChangeEvents live as ``DBLog`` rows with
``log_level="CHANGE_EVENT"`` in the logging database. Reads go through
``ChangeEventLogger.find_all`` and are surfaced via the ``ChangeEventEnvelope``
wire shape.
"""

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import MissingAuthenticationError
from app.api.utils import resolve_principal_id
from app.schemas.policy import Resource, Subject
from app.services.change_event_logger import (
    ChangeEventEnvelope,
    envelope_from_dblog,
    get_change_event_logger,
    is_change_event_enabled,
)
from app.services.pagination import build_paginated_response
from app.services.policy_engine import evaluate as policy_evaluate

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

    # Kill switch: when ChangeEvents are disabled, the audit log is empty.
    if not is_change_event_enabled():
        empty: List[ChangeEventEnvelope] = []
        _empty_page, empty_response = build_paginated_response(
            empty, cursor, limit, _envelope_key
        )
        empty_response["events"] = []
        return empty_response

    ce_logger = get_change_event_logger()
    rows = await ce_logger.find_all(scope=scope, actor_kind=actor_kind)
    candidates = [envelope_from_dblog(r) for r in rows]

    action_f = (action or "").strip()
    resource_id_f = (resource_id or "").strip()
    resource_type_f = (resource_type or "").strip()
    if action_f or resource_id_f or resource_type_f:
        filtered: List[ChangeEventEnvelope] = []
        for ev in candidates:
            if action_f and (ev.action or "") != action_f:
                continue
            if resource_id_f and (ev.resource_id or "") != resource_id_f:
                continue
            if resource_type_f and (ev.resource_type or "") != resource_type_f:
                continue
            filtered.append(ev)
        candidates = filtered

    # Permission filter — D-07 routes per-event through policy_engine.evaluate.
    # The engine's default-human branch dispatches on resource.scope prefix
    # (track:/space:/user:) — verbatim replacement of the legacy decision tree.
    permitted: List[ChangeEventEnvelope] = []
    for ev in candidates:
        decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action="audit_log.read",
            resource=Resource(
                kind="change_event",
                id=ev.id or "",
                scope=ev.scope or "",
            ),
        )
        if decision.allowed:
            permitted.append(ev)

    # Sort by (ts, id) DESC for audit-log convention (most recent first).
    permitted.sort(key=lambda e: (e.ts or "", e.id or ""), reverse=True)

    page, response = build_paginated_response(permitted, cursor, limit, _envelope_key)
    response["events"] = [ev.to_wire_flat() for ev in page]
    return response
