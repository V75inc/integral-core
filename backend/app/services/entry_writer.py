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

Create and update converge on the canonical typed entry commands used by
HTTP and connector sync. Their field validation, revisions and graph writes
are shared; this module owns approval policy and original actor attribution.

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
    from app.schemas.provenance import Provenance
    from app.services.entry_create import create_entry_in_track

    entry = await create_entry_in_track(
        track=track,
        user_id=actor_id,
        actor_kind=actor_kind,
        actor_id=actor_id,
        title=payload.get("title", ""),
        body=payload.get("body", "") or "",
        type_id=payload.get("type_id", ""),
        tags=payload.get("tags") or [],
        custom_fields=payload.get("custom_fields") or {},
        attachment_ids=payload.get("attachment_ids") or [],
        workspace_id=track.workspace_id,
        provenance=(
            Provenance.model_validate(payload["provenance"])
            if payload.get("provenance")
            else None
        ),
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

    track = await Track.get(entry.track_id) if entry.track_id else None
    if not track:
        raise ValueError(f"update_entry_internal: track {entry.track_id!r} not found")
    from app.services.migration_write_guard import assert_track_schema_writable

    await assert_track_schema_writable(track)

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

    from app.services.entry_update import update_entry_in_track

    entry = await update_entry_in_track(
        entry_id=entry_id,
        user_id=actor_id,
        actor_id=actor_id,
        actor_kind=actor_kind,
        workspace_id=track.workspace_id,
        **{
            key: payload[key]
            for key in (
                "title",
                "body",
                "description",
                "custom_fields",
                "tags",
                "type_id",
                "attachment_ids",
                "status",
                "expected_record_revision",
                "expected_schema_revision",
            )
            if key in payload
        },
    )
    return await entry.export(flat=True)


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
