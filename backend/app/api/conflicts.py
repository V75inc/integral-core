"""Phase 5 Plan 05-03 — Conflict REST surface (core).

Endpoints:
- ``GET /api/conflicts?connector_id=&status=`` — list
- ``GET /api/conflicts/{id}``                  — read one
- ``POST /api/conflicts/{id}/resolve``         — resolve (body: ``{resolution}``)

The ``resolve`` endpoint gates via ``policy_engine.evaluate(
action="conflict.resolve", resource=Resource(kind="entry",
id=<conflict.entry_id>))`` — the resolve permission inherits from the
underlying Entry's edit permission (locked decision §Q10): a user who can
edit the Entry can resolve a Conflict on it.

When the resolution is ``applied_external``, the handler writes the
external snapshot back into the Entry and emits a single
``entry.update`` ChangeEvent (carrying ``details.resolved_conflict_id``).
``conflict.resolve`` is intentionally NOT a ChangeEventAction member —
the audit trail uses ``entry.update`` (locked decision #5; see I-SYNC-04).
"""

import logging
from typing import Any, Dict, Optional

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceNotFoundError,
)
from app.api.utils import resolve_principal_id
from app.models.nodes import Conflict, Entry
from app.schemas.policy import Resource, Subject
from app.services.change_event import emit_change_event
from app.services.connectors.conflict_records import (
    list_conflicts,
    resolve_conflict,
)
from app.services.policy_engine import evaluate

logger = logging.getLogger(__name__)


async def _export_conflict(c: Conflict) -> Dict[str, Any]:
    """Stable wire shape for a Conflict — relies on jvspatial ``export(flat=True)``
    so the consumer sees top-level fields (``id``, ``connector_id``, …) rather
    than buried inside ``context``."""
    return await c.export(flat=True)


async def _filter_conflicts_for_user(user_id: str, conflicts: list) -> list:
    """Return conflicts whose underlying entry is readable by ``user_id``."""
    subject = Subject(kind="human", id=user_id)
    visible = []
    for conflict in conflicts:
        entry_id = getattr(conflict, "entry_id", None) or ""
        if not entry_id:
            continue
        decision = await evaluate(
            subject=subject,
            action="entry.read",
            resource=Resource(
                kind="entry",
                id=entry_id,
                scope=f"entry:{entry_id}",
            ),
        )
        if decision.allowed:
            visible.append(conflict)
    return visible


@endpoint("/conflicts", methods=["GET"], auth=True, tags=["Conflicts"])
async def list_all(
    request: Request,
    connector_id: Optional[str] = None,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    """List Conflicts, optionally filtered by ``connector_id`` and/or ``status``.

    Auth required. Results are filtered to conflicts whose underlying entry
    is readable by the caller (``entry.read`` policy gate per conflict).
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    conflicts = await list_conflicts(connector_id=connector_id, status=status)
    conflicts = await _filter_conflicts_for_user(user_id, conflicts)
    return {
        "conflicts": [await _export_conflict(c) for c in conflicts],
    }


@endpoint("/conflicts/{conflict_id}", methods=["GET"], auth=True, tags=["Conflicts"])
async def get_one(request: Request, conflict_id: str) -> Dict[str, Any]:
    """Fetch a single Conflict by id."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    conflict = await Conflict.get(conflict_id)
    if conflict is None:
        raise ResourceNotFoundError(message="Conflict not found")
    visible = await _filter_conflicts_for_user(user_id, [conflict])
    if not visible:
        raise InsufficientPermissionsError(message="Access denied")
    return {"conflict": await _export_conflict(conflict)}


@endpoint(
    "/conflicts/{conflict_id}/resolve",
    methods=["POST"],
    auth=True,
    tags=["Conflicts"],
)
async def resolve(request: Request, conflict_id: str) -> Dict[str, Any]:
    """Resolve a Conflict.

    Body: ``{"resolution": "kept_local" | "applied_external" | "merged"}``.

    - ``kept_local``       — Conflict marked resolved; Entry NOT modified.
    - ``applied_external`` — Entry overwritten with ``external_snapshot``;
      single ``entry.update`` ChangeEvent emitted (the audit trail for the
      resolution event — see I-SYNC-04). Conflict marked resolved.
    - ``merged``           — Conflict marked resolved; the caller is
      expected to have already written the merged payload to the Entry
      via the standard update path (v1 — no automation).

    Policy gate: ``conflict.resolve`` PolicyAction on the underlying Entry
    (kind=``entry``, id=``conflict.entry_id``).
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    resolution = (body or {}).get("resolution", "")
    if resolution not in {"kept_local", "applied_external", "merged"}:
        raise BadRequestError(
            message="resolution must be one of kept_local|applied_external|merged"
        )

    conflict = await Conflict.get(conflict_id)
    if conflict is None:
        raise ResourceNotFoundError(message="Conflict not found")

    decision = await evaluate(
        subject=Subject(kind="human", id=user_id),
        action="conflict.resolve",
        resource=Resource(
            kind="entry",
            id=conflict.entry_id,
            scope=f"entry:{conflict.entry_id}",
        ),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(message="Policy denied conflict.resolve")

    # applied_external writes the external snapshot back into the Entry
    # via the standard path + emits the single entry.update ChangeEvent
    # (D-05 single-emission preserved — see I-SYNC-04).
    if resolution == "applied_external":
        entry = await Entry.get(conflict.entry_id)
        if entry is not None:
            before = {
                "title": entry.title,
                "body": entry.body,
                "tags": list(entry.tags or []),
                "custom_fields": dict(entry.custom_fields or {}),
            }
            ext = conflict.external_snapshot or {}
            entry.title = ext.get("title", entry.title)
            entry.body = ext.get("body", entry.body)
            if "tags" in ext:
                entry.tags = list(ext.get("tags") or [])
            if "custom_fields" in ext:
                entry.custom_fields = dict(ext.get("custom_fields") or {})
            await entry.save()
            await emit_change_event(
                actor_kind="human",
                actor_id=user_id,
                action="entry.update",
                resource_type="Entry",
                resource_id=entry.id,
                before=before,
                after={
                    "title": entry.title,
                    "body": entry.body,
                    "tags": list(entry.tags or []),
                    "custom_fields": dict(entry.custom_fields or {}),
                },
                scope=f"track:{entry.track_id}",
                details={
                    "resolved_conflict_id": conflict.id,
                    "resolution": "applied_external",
                },
            )

    await resolve_conflict(
        conflict=conflict, resolution=resolution, resolver_user_id=user_id
    )
    return {
        "conflict_id": conflict.id,
        "status": "resolved",
        "resolution": resolution,
    }
