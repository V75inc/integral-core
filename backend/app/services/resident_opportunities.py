"""Bounded discovery of current workspace work the resident can suggest."""

import json
from datetime import date
from typing import Any

from jvspatial.core.context import get_default_context

from app.agentive.edges import ResidentAssignment
from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.middleware.permissions_cache import isolated_permissions_cache
from app.services.event_history_cursor import PREFIX as PRIVATE_CURSOR_PREFIX
from app.services.event_history_cursor import (
    decode_history_cursor,
    encode_history_cursor,
)
from app.services.resident_assignment import (
    get_resident_assignment,
    read_declared_task,
    resident_assignment_review_scope,
)
from app.services.resident_opportunity_actions import opportunity_receipt_id
from app.services.workspace_permissions import can_access_workspace

_SCAN_LIMIT = 24
_RESULT_LIMIT = 3


async def list_resident_opportunities(
    *, user_id: str, workspace_id: str | None = None, cursor: str | None = None
) -> dict:
    """Find up to three sponsored assignments awaiting a preparation review."""
    with isolated_permissions_cache():
        if (
            not workspace_id
            or await can_access_workspace(user_id, workspace_id) == "none"
        ):
            raise InsufficientPermissionsError(
                message="An accessible workspace is required"
            )

    binding = {
        "surface": "resident-opportunities-v1",
        "workspace_id": workspace_id,
        "user_id": user_id,
    }
    after, pending = _read_cursor(cursor, binding=binding)
    database = get_default_context().database
    query: dict[str, Any] = {
        "entity": ResidentAssignment._entity_name(),
        "target": workspace_id,
        "context.state": "assigned",
        "context.sponsor_principal_id": user_id,
    }
    if pending:
        # Drain previously ranked candidates before scanning new edges. Source
        # selection and eligibility are still rechecked at the time of reading.
        rows = await database.find(
            "edge", {**query, "id": {"$in": pending}}, limit=_SCAN_LIMIT
        )
        scan_complete = not after
    else:
        if after:
            query["id"] = {"$gt": after}
        rows = await database.find(
            "edge", query, sort=[("id", 1)], limit=_SCAN_LIMIT + 1
        )
        scan_complete = len(rows) <= _SCAN_LIMIT
        after = "" if scan_complete else rows[_SCAN_LIMIT - 1]["id"]

    candidates = []
    for row in rows[:_SCAN_LIMIT]:
        edge_id = row.get("id")
        entry_id = row.get("source")
        revision = (row.get("context") or {}).get("revision")
        if (
            not isinstance(edge_id, str)
            or not edge_id
            or not isinstance(entry_id, str)
            or not entry_id
            or type(revision) is not int
        ):
            continue
        try:
            with isolated_permissions_cache():
                async with resident_assignment_review_scope(
                    workspace_id=workspace_id,
                    entry_id=entry_id,
                    sponsor=user_id,
                    assignment_revision=revision,
                ) as source:
                    assignment = await get_resident_assignment(
                        user_id=user_id, workspace_id=workspace_id, entry_id=entry_id
                    )
                    if not assignment.can_request_review:
                        continue
                    # Only suggest work before its first preparation review.
                    # Any persisted review for this intake owns the next step.
                    review = await get_default_context().database.find_one(
                        "object",
                        {
                            "entity": "WorkItem",
                            "context.kind": "resident_assignment",
                            "context.origin": "mandate_review",
                            "context.workspace_id": workspace_id,
                            "context.principal_id": user_id,
                            "context.plan.mandate_revision.assignment_source.intake_fact_id": source.intake_fact_id,
                        },
                    )
                    if review:
                        continue
                    receipt = await get_default_context().database.get(
                        "object",
                        opportunity_receipt_id(user_id, workspace_id, entry_id),
                    )
                    fact = (receipt or {}).get("context") or {}
                    accepted = bool(fact.get("accepted_at")) and (
                        await read_declared_task(user_id, workspace_id, entry_id)
                        == (source.track_id, fact.get("fingerprint"))
                    )
                    due = source.due_date if isinstance(source.due_date, str) else ""
                    try:
                        due_date = date.fromisoformat(due).isoformat()
                    except ValueError:
                        due_date = None
                    candidates.append(
                        {
                            "edge_id": edge_id,
                            "entry_id": source.entry_id,
                            "track_id": source.track_id,
                            "assignment_revision": source.assignment_revision,
                            "entry_revision": source.entry_revision,
                            "source_fingerprint": source.fingerprint(),
                            "due_date": due_date,
                            "reason": "assigned_awaiting_review",
                            "suggested_action": "request_preparation_review",
                            **({"accepted": True} if accepted else {}),
                        }
                    )
        except (
            InsufficientPermissionsError,
            ResourceNotFoundError,
            ResourceConflictError,
        ):
            # The search hit may be stale. Never return unverified evidence.
            continue

    candidates.sort(
        key=lambda item: (
            not item.get("accepted"),
            item["due_date"] or "9999-12-31",
            item["entry_id"],
        )
    )
    pending = [item["edge_id"] for item in candidates[_RESULT_LIMIT:]]
    result: dict[str, Any] = {
        "opportunities": [
            {key: value for key, value in item.items() if key != "edge_id"}
            for item in candidates[:_RESULT_LIMIT]
        ],
        "scan_complete": scan_complete,
        "has_more_eligible": bool(pending),
    }
    if pending or after:
        result["next_cursor"] = encode_history_cursor(
            json.dumps([after, pending], separators=(",", ":")), binding=binding
        )
    return result


def _read_cursor(
    cursor: str | None, *, binding: dict[str, Any]
) -> tuple[str, list[str]]:
    if cursor is None:
        return "", []
    try:
        if not cursor.startswith(PRIVATE_CURSOR_PREFIX):
            raise ValueError("Expected a private cursor")
        after, pending = json.loads(decode_history_cursor(cursor, binding=binding))
        if (
            not isinstance(after, str)
            or not isinstance(pending, list)
            or len(pending) > _SCAN_LIMIT - _RESULT_LIMIT
            or any(not isinstance(edge_id, str) or not edge_id for edge_id in pending)
        ):
            raise ValueError("Unexpected cursor shape")
    except (BadRequestError, TypeError, ValueError) as exc:
        raise BadRequestError(message="Invalid resident opportunity cursor") from exc
    return after, pending
