"""Workspace member provision + onboarding prompt (recruitment hire on Core)."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import Request
from jvspatial.api import endpoint
from pydantic import ValidationError

from app.api.errors import (
    BadRequestError,
    MissingAuthenticationError,
    ResourceNotFoundError,
)
from app.api.utils import resolve_principal_id
from app.models.edges import IS_MEMBER_OF
from app.models.nodes import User
from app.services.change_event import emit_change_event
from app.services.member_provision import provision_workspace_member
from app.services.onboarding_prompt import (
    pending_onboarding_form_view,
    set_pending_onboarding_form,
)
from app.services.workspace_kind import is_collaborative_kind


@endpoint(
    "/workspaces/{workspace_id}/members/provision",
    methods=["POST"],
    auth=True,
    tags=["Workspaces"],
)
async def provision_workspace_member_route(
    request: Request, workspace_id: str
) -> Dict[str, Any]:
    """Create or reuse a login account and add them as a workspace member."""
    from app.api.workspaces import _require_workspace_role, _validate_member_role
    from app.schemas.api.workspaces_hire import ProvisionWorkspaceMemberRequest

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    ws, _ = await _require_workspace_role(workspace_id, user_id, "admin")
    if not is_collaborative_kind(ws.kind):
        raise BadRequestError(message="Personal workspaces have no members to manage")

    try:
        body = ProvisionWorkspaceMemberRequest.model_validate(await request.json())
    except ValidationError as exc:
        raise BadRequestError(
            message="Validation failed",
            details={"errors": exc.errors()},
        ) from exc

    validated_role = _validate_member_role(body.role)
    result = await provision_workspace_member(
        workspace=ws,
        email=str(body.email),
        display_name=body.display_name,
        role=validated_role,
        send_credentials=body.send_credentials,
        welcome_message=body.welcome_message,
        credentials_email=(
            str(body.credentials_email) if body.credentials_email else None
        ),
        actor_id=user_id,
    )
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="workspace.member_provision",
        resource_type="Workspace",
        resource_id=ws.id,
        before=None,
        after={
            "workspace_id": ws.id,
            "member_user_id": result["user_id"],
            "email": result["email"],
            "created": result["created"],
        },
        scope=f"user:{user_id}",
    )
    return {
        "message": "Member provisioned" if result["created"] else "Member added",
        "workspace_id": ws.id,
        **result,
    }


async def _set_member_assigned_form_prompt(
    request: Request,
    workspace_id: str,
    member_user_id: str,
) -> Dict[str, Any]:
    """Store a pending assigned-form URL on a workspace member."""
    from app.api.workspaces import _require_workspace_role
    from app.schemas.api.workspaces_hire import SetMemberAssignedFormPromptRequest

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    ws, _ = await _require_workspace_role(workspace_id, user_id, "admin")
    if not is_collaborative_kind(ws.kind):
        raise BadRequestError(message="Personal workspaces have no members to manage")

    try:
        body = SetMemberAssignedFormPromptRequest.model_validate(await request.json())
    except ValidationError as exc:
        raise BadRequestError(
            message="Validation failed",
            details={"errors": exc.errors()},
        ) from exc

    member = await User.get(member_user_id)
    if not member:
        raise ResourceNotFoundError(message="Member not found")

    ctx = await member.get_context()
    edges = await ctx.find_edges_between(
        source_id=member.id,
        target_id=ws.id,
        edge_class=IS_MEMBER_OF,
    )
    if not edges:
        raise ResourceNotFoundError(message="Member not found in this workspace")

    await set_pending_onboarding_form(member, body.form_url)
    await member.save()
    view = pending_onboarding_form_view(member)
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="workspace.member_update",
        resource_type="Workspace",
        resource_id=ws.id,
        before=None,
        after={"member_user_id": member.id, "pending": bool(view)},
        scope=f"user:{user_id}",
    )

    emailed = False
    if body.send_email and body.recipient_email:
        from app.services.onboarding_prompt import email_member_onboarding_form_link

        emailed = await email_member_onboarding_form_link(
            recipient_email=str(body.recipient_email),
            form_url=body.form_url,
            workspace_id=ws.id,
            actor_user_id=user_id,
            recipient_name=body.recipient_name,
            form_title=body.form_title,
            member_user_id=member.id,
        )

    payload: Dict[str, Any] = {
        "member_user_id": member.id,
        "pending_assigned_form": view,
        "emailed": emailed,
    }
    if view:
        payload["pending_onboarding_form"] = view
    return payload


@endpoint(
    "/workspaces/{workspace_id}/members/{member_user_id}/assigned-form-prompt",
    methods=["POST"],
    auth=True,
    tags=["Workspaces"],
)
async def set_member_assigned_form_prompt_route(
    request: Request,
    workspace_id: str,
    member_user_id: str,
) -> Dict[str, Any]:
    return await _set_member_assigned_form_prompt(request, workspace_id, member_user_id)


@endpoint(
    "/workspaces/{workspace_id}/members/{member_user_id}/onboarding-form-prompt",
    methods=["POST"],
    auth=True,
    tags=["Workspaces"],
)
async def set_member_onboarding_form_prompt_route(
    request: Request,
    workspace_id: str,
    member_user_id: str,
) -> Dict[str, Any]:
    return await _set_member_assigned_form_prompt(request, workspace_id, member_user_id)
