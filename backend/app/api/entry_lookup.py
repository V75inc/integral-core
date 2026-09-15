"""Batch relation-target lookup.

The frontend ``useRelationLabels`` hook otherwise issues one ``GET /entries/{id}``
(or ``GET /tracks/{id}``) per distinct relation target — N requests on a table or
board view with relation columns. This endpoint resolves a whole set of ids in a
single permission-filtered round trip. Read-only; each target is gated with the
same ``policy_evaluate(*.read)`` decision the per-id endpoints use, so the result
never leaks a target the caller could not already open.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Literal

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import MissingAuthenticationError
from app.api.utils import resolve_principal_id
from app.models.nodes import Entry, Track
from app.schemas.policy import Resource, Subject
from app.services.policy_engine import evaluate as policy_evaluate

_MAX_IDS = 200


async def _allowed(
    user_id: str, kind: Literal["entry", "track"], rid: str, scope: str
) -> bool:
    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action=f"{kind}.read",
        resource=Resource(kind=kind, id=rid, scope=scope),
    )
    return bool(decision.allowed)


@endpoint("/entry-lookup", methods=["POST"], auth=True, tags=["Entries"])
async def relation_target_lookup(request: Request) -> Dict[str, Any]:
    """Resolve relation-target ids to minimal labels, permission-filtered.

    Body: ``{"ids": [...], "kind": "entry" | "track"}``. Returns
    ``{"targets": [{id, title, ...}]}`` for the accessible subset; the frontend
    formats the display label (so label logic stays in one place).
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    raw_ids = payload.get("ids") if isinstance(payload, dict) else None
    kind = (payload.get("kind") if isinstance(payload, dict) else None) or "entry"

    ids = list(dict.fromkeys(i for i in (raw_ids or []) if i))[:_MAX_IDS]
    if not ids:
        return {"targets": []}

    targets: List[Dict[str, Any]] = []

    if str(kind) == "track":
        found = await Track.find({"id": {"$in": ids}})
        allowed = await asyncio.gather(
            *(_allowed(user_id, "track", t.id, "") for t in found)
        )
        for t, ok in zip(found, allowed):
            if ok:
                targets.append(
                    {"id": t.id, "title": (getattr(t, "title", "") or "").strip()}
                )
        return {"targets": targets}

    found = await Entry.find({"id": {"$in": ids}})
    track_ids = list({e.track_id for e in found if getattr(e, "track_id", None)})
    tracks = await Track.find({"id": {"$in": track_ids}}) if track_ids else []
    track_title = {t.id: (getattr(t, "title", "") or "").strip() for t in tracks}
    allowed = await asyncio.gather(
        *(_allowed(user_id, "entry", e.id, f"track:{e.track_id or ''}") for e in found)
    )
    for e, ok in zip(found, allowed):
        if not ok:
            continue
        tid = getattr(e, "track_id", None)
        targets.append(
            {
                "id": e.id,
                "title": (getattr(e, "title", "") or "").strip(),
                "body": (getattr(e, "body", "") or "")[:120],
                "track_id": tid,
                "track_title": track_title.get(tid or "", ""),
            }
        )
    return {"targets": targets}
