"""Phase 7 Plan 07-04 — pure-service helper for track.create.

Used by ``approval_executor.execute_approval`` on the approve path. The
helper threads ``_internal_actor`` through ``policy_evaluate`` which
short-circuits the recursion guard on re-run.

STANDALONE pure-service function, NOT an internal HTTP self-call. The
helper implements the substrate-level write only (minimum viable for
re-run); full handler-level type_hint resolution, template provisioning,
and content_profile attachment are NOT replicated. v1 limitation: an
agent's track.create with type_hint / template_id / explicit picker fields
will fall back to the default empty profile on approve re-run; future
hardening expands helper parity.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.models.edges import OWNS
from app.models.nodes import Track, User, Workspace
from app.schemas.policy import Resource, Subject
from app.services.change_event import emit_change_event
from app.services.policy_engine import evaluate as policy_evaluate
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


async def create_track_internal(
    *,
    actor_kind: str,
    actor_id: str,
    payload: Dict[str, Any],
    _internal_actor: Optional[Subject] = None,
) -> Dict[str, Any]:
    """Pure-service create_track.

    Required payload keys:
      - ``title`` (str)
      - ``workspace_id`` (str)  [resolved at agent submit time]
    Optional payload keys:
      - ``purpose`` (str), ``icon`` (str), ``visibility`` (str)
      - ``app_id`` (str) — link to parent App via CONTAINS
    """
    title = payload.get("title", "")
    workspace_id = payload.get("workspace_id", "")
    if not workspace_id:
        raise ValueError("create_track_internal: payload missing 'workspace_id'")

    decision = await policy_evaluate(
        subject=Subject(kind=actor_kind, id=actor_id),  # type: ignore[arg-type]
        action="track.create",
        resource=Resource(
            kind="track",
            id="",
            scope=f"workspace:{workspace_id}",
        ),
        payload=payload,
        _internal_actor=_internal_actor,
    )
    if not decision.allowed:
        raise PermissionError(
            f"create_track_internal: policy denied (reason={decision.reason!r})"
        )

    workspace = await Workspace.get(workspace_id)
    if not workspace:
        raise ValueError(f"create_track_internal: workspace {workspace_id!r} not found")

    now = utc_now_iso()
    track = await Track.create(
        title=title or "Untitled Track",
        purpose=payload.get("purpose", ""),
        icon=payload.get("icon", ""),
        visibility=payload.get("visibility", "private"),
        owner_id=actor_id,
        workspace_id=workspace_id,
        created_at=now,
        updated_at=now,
    )

    # Mirror existing handler: OWNS edge from author (the agent) to track.
    user = await User.get(actor_id)
    if user:
        await user.connect(track, edge=OWNS, owned_since=now)

    await emit_change_event(
        actor_kind=actor_kind,  # type: ignore[arg-type]
        actor_id=actor_id,
        action="track.create",
        resource_type="Track",
        resource_id=track.id,
        before=None,
        after=await track.export(flat=True),
        scope=f"workspace:{workspace_id}",
    )
    return await track.export(flat=True)
