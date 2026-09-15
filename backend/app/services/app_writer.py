"""Phase 7 Plan 07-04 — pure-service helper for app_node.create.

Used by ``approval_executor.execute_approval`` on the approve path.
STANDALONE pure-service function, NOT an internal HTTP self-call.

v1 limitation: only the substrate-level app create is replicated.
Full handler-level content_profile auto-attachment and multi-track
provisioning (per Phase 2.1 Plan 02.1-02) are NOT replicated here; future
hardening expands helper parity.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.models.nodes import App, Workspace
from app.schemas.policy import Resource, Subject
from app.services.app_graph import wire_app_owner
from app.services.change_event import emit_change_event
from app.services.policy_engine import evaluate as policy_evaluate
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


async def create_app_internal(
    *,
    actor_kind: str,
    actor_id: str,
    payload: Dict[str, Any],
    _internal_actor: Optional[Subject] = None,
) -> Dict[str, Any]:
    """Pure-service create_space.

    Required payload keys:
      - ``title`` (str)
      - ``workspace_id`` (str)
    Optional payload keys:
      - ``description`` (str), ``icon`` (str), ``visibility`` (str)
    """
    title = payload.get("title", "")
    workspace_id = payload.get("workspace_id", "")
    if not workspace_id:
        raise ValueError("create_app_internal: payload missing 'workspace_id'")

    decision = await policy_evaluate(
        subject=Subject(kind=actor_kind, id=actor_id),  # type: ignore[arg-type]
        action="app.create",
        resource=Resource(
            kind="app",
            id="",
            scope=f"workspace:{workspace_id}",
        ),
        payload=payload,
        _internal_actor=_internal_actor,
    )
    if not decision.allowed:
        raise PermissionError(
            f"create_app_internal: policy denied (reason={decision.reason!r})"
        )

    workspace = await Workspace.get(workspace_id)
    if not workspace:
        raise ValueError(f"create_app_internal: workspace {workspace_id!r} not found")

    now = utc_now_iso()
    app_node = await App.create(
        title=title or "Untitled App",
        description=payload.get("description", ""),
        icon=payload.get("icon", ""),
        visibility=payload.get("visibility", "private"),
        owner_id=actor_id,
        workspace_id=workspace_id,
        created_at=now,
        updated_at=now,
    )

    # Route through wire_app_owner rather than connecting here: `User.get`
    # returns None for an `o.User.*` principal id, so this silently produced an
    # App with no OWNS edge whenever the caller held an auth id rather than a
    # graph profile id. wire_app_owner resolves either form, falls back to the
    # workspace owner, and raises rather than leaving the App unadministrable.
    await wire_app_owner(app_node, actor_id, workspace_id=workspace_id)

    await emit_change_event(
        actor_kind=actor_kind,  # type: ignore[arg-type]
        actor_id=actor_id,
        action="app.create",
        resource_type="App",
        resource_id=app_node.id,
        before=None,
        after=await app_node.export(flat=True),
        scope=f"workspace:{workspace_id}",
    )
    return await app_node.export(flat=True)
