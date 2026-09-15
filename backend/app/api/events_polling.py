"""Polling endpoint for ChangeEvents (EVT-02).

GET /api/events?since=<cursor>&scope=...&actor_kind=...&limit=N

Cursor format: opaque base64-encoded {ts, id} (D-08 — direct reuse of
app/services/pagination.py::encode_cursor / decode_cursor / build_paginated_response).

Empty cursor = "from beginning of retention window" — the polling endpoint
returns events in ASC ``(ts, id)`` order so cron-style agents (forward-stream
consumers) can iterate from oldest to newest. Counterpart of /api/audit-log
which sorts DESC.

Per CONTEXT D-07: same per-event permission filter as audit-log + WS broadcast.
Per CONTEXT D-08: page size capped at EVENT_FEED_PAGE_SIZE (default 100).
Per CONTEXT D-13 / D-12: this is core infrastructure, available without
AGENTIVE_ENABLED.

Storage shape: ChangeEvents live as ``DBLog`` rows with
``log_level="CHANGE_EVENT"`` in the logging database. Reads go through
``ChangeEventLogger.find_all`` and are surfaced via the ``ChangeEventEnvelope``
wire shape.
"""

from __future__ import annotations

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


def _event_feed_page_size_default() -> int:
    try:
        return int(os.getenv("EVENT_FEED_PAGE_SIZE", "100"))
    except ValueError:
        return 100


def _envelope_key(env: ChangeEventEnvelope) -> Tuple[str, str]:
    """Sort/cursor key for ChangeEventEnvelope — (id, ts). Matches audit-log convention."""
    return (env.id or "", env.ts or "")


@endpoint("/events", methods=["GET"], auth=True, tags=["Events"])
async def list_events_polling(
    request: Request,
    since: Optional[str] = None,
    scope: Optional[str] = None,
    actor_kind: Optional[str] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Return ChangeEvents in ASC (ts, id) order for forward-stream consumers."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    page_cap = _event_feed_page_size_default()
    if limit is None or limit > page_cap:
        limit = page_cap
    if limit < 1:
        limit = 1

    # Kill switch: when ChangeEvents are disabled, the event feed is empty.
    if not is_change_event_enabled():
        empty: List[ChangeEventEnvelope] = []
        _empty_page, empty_response = build_paginated_response(
            empty, since, limit, _envelope_key
        )
        empty_response["events"] = []
        return empty_response

    ce_logger = get_change_event_logger()
    rows = await ce_logger.find_all(scope=scope, actor_kind=actor_kind)
    candidates = [envelope_from_dblog(r) for r in rows]

    # Permission filter — D-07 routes per-event through policy_engine.evaluate.
    # The engine's default-human branch dispatches on resource.scope prefix
    # (track:/space:/user:) — verbatim replacement of the legacy decision tree.
    permitted: List[ChangeEventEnvelope] = []
    for ev in candidates:
        decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action="event_feed.subscribe",
            resource=Resource(
                kind="change_event",
                id=ev.id or "",
                scope=ev.scope or "",
            ),
        )
        if decision.allowed:
            permitted.append(ev)

    # ASC ordering — forward-stream (oldest first). Tie-break by id.
    permitted.sort(key=lambda e: (e.ts or "", e.id or ""))

    page, response = build_paginated_response(permitted, since, limit, _envelope_key)
    response["events"] = [ev.to_wire_flat() for ev in page]
    return response
