"""Authorization and safe projections for durable work observation."""

from __future__ import annotations

from app.agentive.work_models import WorkItem
from app.api.errors import InsufficientPermissionsError, ResourceNotFoundError
from app.schemas.agentive.work import WorkFailure, WorkItemStatusResponse
from app.services.workspace_permissions import can_access_workspace


def _work_object_id(work_item_id: str) -> str:
    return (
        work_item_id
        if work_item_id.startswith("o.WorkItem.")
        else f"o.WorkItem.{work_item_id}"
    )


async def get_visible_work_item(
    *, work_item_id: str, principal_id: str
) -> WorkItemStatusResponse:
    """Return a safe work projection when the caller may observe it.

    A work initiator may always inspect their own item. Workspace owners and
    admins may inspect items in their workspace for operational support. Other
    members cannot enumerate one another's work, and callers outside the
    workspace receive the same not-found response as for an unknown id.
    """
    item = await WorkItem.get(_work_object_id(work_item_id))
    if item is None:
        raise ResourceNotFoundError(message="Work item not found")

    workspace_role = await can_access_workspace(principal_id, item.workspace_id)
    if workspace_role == "none":
        raise ResourceNotFoundError(message="Work item not found")
    if principal_id != item.principal_id and workspace_role not in ("owner", "admin"):
        raise InsufficientPermissionsError(
            message="Caller may not inspect this work item"
        )

    failure = WorkFailure.model_validate(item.failure) if item.failure else None
    return WorkItemStatusResponse(
        work_item_id=item.work_item_id,
        kind=item.kind,  # type: ignore[arg-type]
        status=item.status,  # type: ignore[arg-type]
        workspace_id=item.workspace_id,
        app_id=item.app_id,
        attempt=item.attempt,
        next_attempt_at=item.next_attempt_at,
        updated_at=item.updated_at,
        result_refs=list(item.result_refs or []),
        failure=failure,
    )
