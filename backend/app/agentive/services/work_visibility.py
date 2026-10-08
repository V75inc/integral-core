"""Authorization and safe projections for durable work observation."""

from __future__ import annotations

from app.agentive.work_models import WorkItem
from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceNotFoundError,
)
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

    return _project_work_status(item.model_dump())


def _project_work_status(fields: dict) -> WorkItemStatusResponse:
    failure = (
        WorkFailure.model_validate(fields["failure"]) if fields.get("failure") else None
    )
    action = (fields.get("input_payload") or {}).get("action", "")
    operation = (
        action
        if fields.get("kind") == "app_lifecycle"
        and action in {"install", "upgrade", "uninstall", "resume"}
        else ""
    )
    return WorkItemStatusResponse(
        operation=operation,
        work_item_id=fields.get("work_item_id", ""),
        kind=fields.get("kind", ""),  # type: ignore[arg-type]
        status=fields.get("status", ""),  # type: ignore[arg-type]
        workspace_id=fields.get("workspace_id", ""),
        app_id=fields.get("app_id", ""),
        attempt=fields.get("attempt", 0),
        next_attempt_at=fields.get("next_attempt_at", ""),
        updated_at=fields.get("updated_at", ""),
        result_refs=list(fields.get("result_refs") or []),
        failure=failure,
    )


async def list_visible_work_items(
    *, workspace_id: str, principal_id: str, limit: int = 50, cursor: str = ""
) -> tuple[list[WorkItemStatusResponse], str | None]:
    """Keyset-page safe state; authorize workspace and constrain members before IO."""
    from jvspatial.core.context import get_default_context

    if not workspace_id or not 1 <= limit <= 100:
        raise BadRequestError(
            message="A workspace and limit from 1 to 100 are required"
        )
    role = await can_access_workspace(principal_id, workspace_id)
    if role == "none":
        raise ResourceNotFoundError(message="Workspace not found")
    query = {"entity": WorkItem._entity_name(), "context.workspace_id": workspace_id}
    if role not in ("owner", "admin"):
        query["context.principal_id"] = principal_id
    try:
        rows, next_cursor = await get_default_context().find_page(
            "object",
            query,
            sort=[("context.updated_at", -1)],
            after=cursor or None,
            limit=limit,
        )
    except ValueError as exc:
        raise BadRequestError(message="Invalid work page cursor") from exc
    return [_project_work_status(row["context"]) for row in rows], next_cursor
