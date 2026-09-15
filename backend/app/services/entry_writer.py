"""Phase 7 Plan 07-04 — pure-service helpers for entry mutations.

Used by ``approval_executor.execute_approval`` on the approve path to
re-run an agent's deferred write. The helpers thread an
``_internal_actor=Subject(kind='system', id='approval_approve')`` through
``policy_engine.evaluate``, which short-circuits to
``Decision(allowed=True, reason='system_subject_internal')`` — bypassing the
original ``requires_human_approval`` intercept that captured the write.

STANDALONE pure-service functions, NOT internal HTTP self-calls. The 3
supported actions:

    entry.create  -> create_entry_internal
    entry.update  -> update_entry_internal
    entry.delete  -> delete_entry_internal

The ChangeEvent emitted by the re-run carries ``actor_kind=ap.actor_kind,
actor_id=ap.actor_id`` (the original agent — NEVER the approving human;
the human's identity surfaces only in the ``approval.approve`` PolicyAction
evaluation, which is gate-only).

Helpers are intentionally minimal — they implement the substrate-level
write only. Full handler-level validation (entry-type lookup, tag
materialization, runtime tier resolution, cross-track guard) is delegated
to the persistence boundary; any logic that would fail in the helper but
succeed in the REST handler is documented inline as a v1 limitation
(future hardening expands the helper surface to full handler parity).

This module's surface is INTERNAL-ONLY — every helper accepts an
``_internal_actor`` kwarg that the executor sets. The kwarg MUST NOT be
exposed via any REST surface; the surface lives only in policy_engine.py
+ change_event.py + approval_executor.py + this module + the 3 sibling
writer modules (track / app / comment).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.models.nodes import Entry, Track
from app.schemas.policy import Resource, Subject
from app.services.change_event import emit_change_event
from app.services.entry_deletion import delete_entry_fast
from app.services.policy_engine import evaluate as policy_evaluate
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


async def create_entry_internal(
    *,
    actor_kind: str,
    actor_id: str,
    payload: Dict[str, Any],
    _internal_actor: Optional[Subject] = None,
) -> Dict[str, Any]:
    """Pure-service create_entry — minimal substrate-level implementation.

    Required payload keys:
      - ``track_id`` (str)
    Optional payload keys:
      - ``title`` (str)
      - ``type_id`` (str)
      - ``body`` (str)
      - ``custom_fields`` (dict)
      - ``tags`` (list[str])
      - ``provenance`` (dict) — who/what produced this row. Omit for a
        human write and the Entry default applies. Supplied when a caller
        writes on somebody's behalf and the row should say so: the actor
        gating the write is the person, the SOURCE of the content is not.

    Returns the exported entry as a flat dict (jvspatial ``export(flat=True)``).
    """
    track_id = payload.get("track_id", "")
    if not track_id:
        raise ValueError("create_entry_internal: payload missing 'track_id'")

    # Policy gate — threads _internal_actor through for the recursion-guard
    # short-circuit on the approve re-run path.
    decision = await policy_evaluate(
        subject=Subject(kind=actor_kind, id=actor_id),  # type: ignore[arg-type]
        action="entry.create",
        resource=Resource(
            kind="entry",
            id="",
            scope=f"track:{track_id}",
        ),
        payload=payload,
        _internal_actor=_internal_actor,
    )
    if not decision.allowed:
        raise PermissionError(
            f"create_entry_internal: policy denied (reason={decision.reason!r})"
        )

    track = await Track.get(track_id)
    if not track:
        raise ValueError(f"create_entry_internal: track {track_id!r} not found")

    now = utc_now_iso()
    entry = await Entry.create(
        type_id=payload.get("type_id", ""),
        title=payload.get("title", ""),
        author_id=actor_id,
        track_id=track_id,
        tags=payload.get("tags") or [],
        custom_fields=payload.get("custom_fields") or {},
        status="active",
        body=payload.get("body", "") or "",
        attachment_ids=payload.get("attachment_ids") or [],
        created_at=now,
        updated_at=now,
        **({"provenance": payload["provenance"]} if payload.get("provenance") else {}),
    )

    # Link to parent track via CONTAINS — mirrors api/entries.py:402.
    from app.models.edges import CONTAINS

    await track.connect(entry, edge=CONTAINS, added_at=now)

    # Emit the ORIGINAL action (entry.create) with the ORIGINAL agent as
    # actor — I-APPROVAL-02. The approving human's identity surfaces ONLY
    # in the separate approval.approve PolicyAction gate evaluation.
    await emit_change_event(
        actor_kind=actor_kind,  # type: ignore[arg-type]
        actor_id=actor_id,
        action="entry.create",
        resource_type="Entry",
        resource_id=entry.id,
        before=None,
        after=await entry.export(flat=True),
        scope=f"track:{track_id}",
    )

    return await entry.export(flat=True)


async def update_entry_internal(
    *,
    actor_kind: str,
    actor_id: str,
    payload: Dict[str, Any],
    _internal_actor: Optional[Subject] = None,
) -> Dict[str, Any]:
    """Pure-service update_entry — minimal substrate-level implementation.

    Required payload keys:
      - ``entry_id`` (str)
    Optional payload keys:
      - ``title`` (str)
      - ``body`` (str)
      - ``custom_fields`` (dict)
    """
    entry_id = payload.get("entry_id", "")
    if not entry_id:
        raise ValueError("update_entry_internal: payload missing 'entry_id'")

    entry = await Entry.get(entry_id)
    if not entry:
        raise ValueError(f"update_entry_internal: entry {entry_id!r} not found")

    decision = await policy_evaluate(
        subject=Subject(kind=actor_kind, id=actor_id),  # type: ignore[arg-type]
        action="entry.update",
        resource=Resource(
            kind="entry",
            id=entry_id,
            scope=f"track:{entry.track_id or ''}",
        ),
        payload=payload,
        _internal_actor=_internal_actor,
    )
    if not decision.allowed:
        raise PermissionError(
            f"update_entry_internal: policy denied (reason={decision.reason!r})"
        )

    before = await entry.export(flat=True)

    # Apply mutation — substrate-level only (full custom-field validation
    # deferred to v2 helper surface; v1 trusts payload semantically).
    if "title" in payload:
        entry.title = payload["title"]
    if "body" in payload:
        entry.body = payload["body"] or ""
    if "custom_fields" in payload and isinstance(payload["custom_fields"], dict):
        entry.custom_fields = payload["custom_fields"]
    if "tags" in payload and isinstance(payload["tags"], list):
        entry.tags = payload["tags"]
    entry.updated_at = utc_now_iso()
    await entry.save()

    after = await entry.export(flat=True)
    await emit_change_event(
        actor_kind=actor_kind,  # type: ignore[arg-type]
        actor_id=actor_id,
        action="entry.update",
        resource_type="Entry",
        resource_id=entry.id,
        before=before,
        after=after,
        scope=f"track:{entry.track_id or ''}",
    )
    return after


async def delete_entry_internal(
    *,
    actor_kind: str,
    actor_id: str,
    payload: Dict[str, Any],
    _internal_actor: Optional[Subject] = None,
) -> Dict[str, Any]:
    """Pure-service delete_entry — minimal substrate-level implementation.

    Required payload keys:
      - ``entry_id`` (str)
    """
    entry_id = payload.get("entry_id", "")
    if not entry_id:
        raise ValueError("delete_entry_internal: payload missing 'entry_id'")

    entry = await Entry.get(entry_id)
    if not entry:
        raise ValueError(f"delete_entry_internal: entry {entry_id!r} not found")

    decision = await policy_evaluate(
        subject=Subject(kind=actor_kind, id=actor_id),  # type: ignore[arg-type]
        action="entry.delete",
        resource=Resource(
            kind="entry",
            id=entry_id,
            scope=f"track:{entry.track_id or ''}",
        ),
        payload=payload,
        _internal_actor=_internal_actor,
    )
    if not decision.allowed:
        raise PermissionError(
            f"delete_entry_internal: policy denied (reason={decision.reason!r})"
        )

    before = await entry.export(flat=True)
    track_id = entry.track_id or ""
    # Single entry-deletion primitive: comments, upload sessions, share
    # links / invitations, anchor cascade and the embedding soft-delete all
    # live in ``delete_entry_fast``. An internal (system-approved) re-run
    # takes the system-subject cascade path; otherwise the original actor
    # is threaded through so ``anchor.cascade`` is evaluated against them.
    if _internal_actor is not None:
        await delete_entry_fast(entry)
    else:
        await delete_entry_fast(entry, actor_user_id=actor_id, actor_kind=actor_kind)

    await emit_change_event(
        actor_kind=actor_kind,  # type: ignore[arg-type]
        actor_id=actor_id,
        action="entry.delete",
        resource_type="Entry",
        resource_id=entry_id,
        before=before,
        after=None,
        scope=f"track:{track_id}",
    )
    return {"deleted": True, "entry_id": entry_id}
