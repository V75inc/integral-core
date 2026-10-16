"""Polling endpoint for ChangeEvents (EVT-02).

GET /api/events?since=<cursor>&scope=...&actor_kind=...&limit=N

New confidential continuation tokens bind the principal, selected workspace,
filters and direction; current permissions are still checked on every read.

Empty cursor = "from beginning of retention window" — the polling endpoint
returns events in ASC ``(ts, id)`` order so cron-style agents (forward-stream
consumers) can iterate from oldest to newest. Counterpart of /api/audit-log
which sorts DESC.

Per CONTEXT D-07: same per-event permission filter as audit-log + WS broadcast.
Per CONTEXT D-08: page size capped at EVENT_FEED_PAGE_SIZE (default 100).
This is always-available Core infrastructure.

Storage shape: ChangeEvents live as ``DBLog`` rows with
``log_level="CHANGE_EVENT"`` in the logging database. Reads go through
bounded ``ChangeEventLogger.find_page`` windows and are surfaced via the
``ChangeEventEnvelope`` wire shape. A continuation may advance through withheld
events without revealing their identifiers or promising another visible result.
"""

from __future__ import annotations

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

    from app.services.event_history import read_event_history
    from app.services.request_scope import resolve_execution_scope_from_request

    execution = await resolve_execution_scope_from_request(
        request, user_id, origin="event_history"
    )

    # Kill switch: when ChangeEvents are disabled, the event feed is empty.
    if not is_change_event_enabled():
        empty: List[ChangeEventEnvelope] = []
        _empty_page, empty_response = build_paginated_response(
            empty, since, limit, _envelope_key
        )
        empty_response["events"] = []
        return empty_response

    return await read_event_history(
        logger=get_change_event_logger(),
        user_id=user_id,
        workspace_id=execution.workspace_id,
        action="event_feed.subscribe",
        cursor=since,
        limit=limit,
        descending=False,
        scope=scope,
        actor_kind=actor_kind,
    )
