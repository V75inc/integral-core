"""Phase 7 Plan 07-04 — TTL reclaim loop for Approval rows.

Mirrors ``backend/app/services/change_event_ttl.ttl_reclaim_loop`` VERBATIM.
Same shape:

    async def approval_ttl_loop() -> None:
        interval = _interval_seconds()
        while True:
            try:
                await expire_old_approvals()
            except Exception as e:
                logger.warning("approval_ttl: cycle failed: %s", e)
            await asyncio.sleep(interval)

TESTING gate inherited from ``_startup`` top in ``app/main.py`` — does
NOT spawn under PYTEST_CURRENT_TEST or TESTING=1. Spawned from
``app/main.py`` next to ``ttl_reclaim_loop`` via ``asyncio.create_task``.

Env vars:
    APPROVAL_TTL_DAYS=7                   (used by policy_engine intercept
                                            when stamping expires_at — this
                                            module reads the row's expires_at
                                            field, not the env var)
    APPROVAL_RECLAIM_INTERVAL_HOURS=1.0   (loop wake interval)

``payload`` is NOT nulled on expiry — approvals are short-lived (TTL 7
days default) and the payload remains forensic record even after expiry.
Only ``status`` flips to ``'expired'`` and an ``approval.expired``
ChangeEvent is emitted.

The approval.expired ChangeEvent is in BROADCAST_SKIP_ACTIONS
(change_event.py) — TTL is a maintenance log, not a subscriber event.
The approval review UI polls GET /api/approvals; expiry visibility is
driven by row status on subsequent reads.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

from app.models.nodes import Approval
from app.services.change_event import emit_change_event
from app.utils.time import utc_now

logger = logging.getLogger(__name__)


# Default TTL — used by policy_engine.evaluate when stamping
# Approval.expires_at. Exported here for documentation; the loop itself
# reads the per-row expires_at, not this constant.
APPROVAL_TTL_DAYS = 7


def _interval_seconds() -> float:
    """Wake interval for the TTL loop in seconds (default 1.0h)."""
    try:
        hours = float(os.getenv("APPROVAL_RECLAIM_INTERVAL_HOURS", "1.0"))
    except ValueError:
        hours = 1.0
    return hours * 3600.0


async def _expiration_workspace(row: dict) -> str:
    """Resolve current rooted scope only for a durable expiration audit receipt."""
    from app.services.resource_location import KNOWN_APP_STATES, locate_resource

    data = row["context"]
    scope = data.get("resource_scope")
    candidates = []
    if isinstance(scope, str) and ":" in scope:
        kind, identity = scope.split(":", 1)
        if kind in {"workspace", "app", "track", "entry"} and identity:
            candidates.append((kind, identity))
    if data.get("resource_kind") in {"app", "track", "entry"} and data.get(
        "resource_id"
    ):
        candidates.append((data["resource_kind"], data["resource_id"]))
    payload = data.get("payload")
    if isinstance(payload, dict):
        for key, kind in (
            ("entry_id", "entry"),
            ("track_id", "track"),
            ("workspace_id", "workspace"),
        ):
            identity = payload.get(key)
            if isinstance(identity, str) and identity:
                candidates.append((kind, identity))
    scopes = set()
    for kind, identity in candidates:
        location = await locate_resource(
            kind, identity, allowed_app_states=KNOWN_APP_STATES
        )
        if location is not None:
            scopes.add(location.workspace_id)
    return scopes.pop() if len(scopes) == 1 else ""


async def expire_old_approvals() -> int:
    """Expire bounded pending rows by exact CAS; never overwrite a decided row."""
    from contextlib import AsyncExitStack

    from jvspatial.core.context import get_default_context

    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
        postgres_graph_transaction,
    )
    from app.services.authority_facts import primary_node_record
    from app.services.workspace_membership import _deliver

    database = get_default_context().database
    candidates = await database.find(
        "node",
        {"entity": Approval._entity_name(), "context.status": "pending"},
        limit=1000,
        sort=[("id", 1)],
    )
    now = utc_now()
    expired = 0
    for candidate in candidates:
        data = candidate.get("context")
        if not isinstance(data, dict):
            continue
        expiry = data.get("expires_at")
        try:
            row_expires = (
                datetime.fromisoformat(expiry) if isinstance(expiry, str) else None
            )
            if row_expires is None or row_expires.tzinfo is None or row_expires > now:
                continue
        except ValueError:
            logger.warning(
                "approval_ttl: malformed expiration on approval=%s", candidate["id"]
            )
            continue
        outbox = None
        changed = None
        async with AsyncExitStack() as stack:
            if graph_transaction_available(database):
                await stack.enter_async_context(postgres_graph_transaction(database))
            held = get_default_context().database
            row = await primary_node_record(held, Approval, candidate["id"])
            if (
                row is None
                or row["context"].get("status") != "pending"
                or row["context"].get("expires_at") != expiry
            ):
                continue
            revision = row["context"].get("decision_revision", 0)
            if type(revision) is not int or revision < 0:
                continue
            query = {
                "id": row["id"],
                "entity": Approval._entity_name(),
                "context.status": "pending",
                "context.expires_at": expiry,
                "context.decision_revision": (
                    revision
                    if "decision_revision" in row["context"]
                    else {"$exists": False}
                ),
            }
            values = {
                "context.status": "expired",
                "context.decision_revision": revision + 1,
            }
            if "status" in row:
                if row["status"] != "pending":
                    continue
                values["status"] = "expired"
            if "decision_revision" in row:
                if row["decision_revision"] != revision:
                    continue
                values["decision_revision"] = revision + 1
            changed = await held.find_one_and_update("node", query, {"$set": values})
            if changed is None:
                continue
            event = {
                "actor_kind": "system",
                "actor_id": "approval_ttl",
                "action": "approval.expired",
                "resource_type": "approval",
                "resource_id": row["id"],
                "before": None,
                "after": None,
                "scope": f"approval:{row['id']}",
                "details": {
                    "original_action": row["context"].get("action", ""),
                    "agent_id": row["context"].get("actor_id", ""),
                    "policy_id": row["context"].get("policy_id", ""),
                },
            }
            workspace_id = await _expiration_workspace(row)
            if workspace_id and graph_transaction_available(held):
                from app.services.app_operations.event_outbox import (
                    insert_revision_event,
                )

                outbox = await insert_revision_event(
                    transaction=held,
                    workspace_id=workspace_id,
                    namespace="approval-expiration-v1",
                    subject_id=row["id"],
                    revision=revision + 1,
                    event=event,
                )
        if changed is not None:
            if outbox:
                await _deliver(outbox)
            else:
                # Legacy unrooted maintenance has no canonical workspace receipt;
                # expiry can only remove authority, never synthesize an admission.
                try:
                    await emit_change_event(**event)
                except Exception:
                    logger.warning(
                        "approval_ttl: expiration audit pending for approval=%s",
                        candidate["id"],
                        exc_info=True,
                    )
            expired += 1
    if expired:
        await get_default_context().clear_cache()
    return expired


async def approval_ttl_loop() -> None:
    """Background loop — wakes every interval, calls expire_old_approvals."""
    interval = _interval_seconds()
    logger.info("approval_ttl: loop started (interval=%.1fs)", interval)
    while True:
        try:
            await expire_old_approvals()
        except Exception as e:  # noqa: BLE001
            logger.warning("approval_ttl: cycle failed: %s", e)
        await asyncio.sleep(interval)
