"""Phase 7 Plan 07-04 — pure-service helper for comment.create.

Used by ``approval_executor.execute_approval`` on the approve path.
STANDALONE pure-service function.

v1 limitation: only top-level comments on an Entry. Threaded replies +
@mentions are NOT replicated; future hardening expands helper parity.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.models.edges import AUTHORED_BY, HAS_COMMENT
from app.models.nodes import Comment, Entry, User
from app.schemas.policy import Resource, Subject
from app.services.change_event import emit_change_event
from app.services.policy_engine import evaluate as policy_evaluate
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


async def create_comment_internal(
    *,
    actor_kind: str,
    actor_id: str,
    payload: Dict[str, Any],
    _internal_actor: Optional[Subject] = None,
) -> Dict[str, Any]:
    """Pure-service create_comment.

    Required payload keys:
      - ``entry_id`` (str)
      - ``body`` (str)
    """
    entry_id = payload.get("entry_id", "")
    body = payload.get("body", "")
    if not entry_id:
        raise ValueError("create_comment_internal: payload missing 'entry_id'")
    if not body:
        raise ValueError("create_comment_internal: payload missing 'body'")

    entry = await Entry.get(entry_id)
    if not entry:
        raise ValueError(f"create_comment_internal: entry {entry_id!r} not found")

    decision = await policy_evaluate(
        subject=Subject(kind=actor_kind, id=actor_id),  # type: ignore[arg-type]
        action="comment.create",
        # Target the ENTRY, matching the REST handler
        # (api/comments.py::create_comment). With ``kind="comment"`` and an
        # empty id the engine had nothing entry-shaped to resolve and fell back
        # to the track named in the scope — which is precisely the path that
        # cannot see a per-ENTRY ``EXCLUDED_FROM`` edge. An approval executed
        # on behalf of an excluded author would have posted the comment that
        # the REST path refuses.
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
            f"create_comment_internal: policy denied (reason={decision.reason!r})"
        )

    now = utc_now_iso()
    comment = await Comment.create(
        body=body,
        author_id=actor_id,
        entry_id=entry_id,
        created_at=now,
        updated_at=now,
    )
    await entry.connect(comment, edge=HAS_COMMENT, posted_at=now)

    user = await User.get(actor_id)
    if user:
        await comment.connect(user, edge=AUTHORED_BY, authored_at=now)

    await emit_change_event(
        actor_kind=actor_kind,  # type: ignore[arg-type]
        actor_id=actor_id,
        action="comment.create",
        resource_type="Comment",
        resource_id=comment.id,
        before=None,
        after=await comment.export(flat=True),
        scope=f"track:{entry.track_id or ''}",
    )
    return await comment.export(flat=True)
