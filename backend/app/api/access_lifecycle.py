"""Explicit ownership consent and retained suspension endpoints."""

from fastapi import Request
from jvspatial.api import endpoint
from pydantic import ValidationError

from app.api.errors import BadRequestError, MissingAuthenticationError
from app.api.utils import resolve_principal_id
from app.schemas.access_lifecycle import (
    OwnershipRequest,
    OwnershipResponse,
    SuspensionRequest,
)
from app.services.request_scope import resolve_workspace_id_from_request


def _actor(request: Request) -> str:
    actor = resolve_principal_id(request)
    if not actor:
        raise MissingAuthenticationError(message="Authentication required")
    return actor


async def _body(request: Request, schema):
    if request.query_params:
        raise BadRequestError(message="Lifecycle commands accept JSON fields only")
    try:
        return schema.model_validate(await request.json())
    except (ValidationError, ValueError) as exc:
        raise BadRequestError(message="Invalid lifecycle request") from exc


async def _scope(request: Request, actor: str, workspace_id: str) -> None:
    if request.headers.get("X-Integral-Scope") is not None:
        if await resolve_workspace_id_from_request(request, actor) != workspace_id:
            raise BadRequestError(message="Workspace scope does not match target")


@endpoint(
    "/workspaces/{workspace_id}/ownership-proposals",
    methods=["POST"],
    auth=True,
    tags=["Workspaces"],
)
async def propose_workspace_ownership(request: Request, workspace_id: str) -> dict:
    """Issue a recipient proposal; retain the current owner until consent."""
    from app.services.ownership_consent import request_ownership_transfer

    actor = _actor(request)
    await _scope(request, actor, workspace_id)
    body = await _body(request, OwnershipRequest)
    return await request_ownership_transfer(
        actor_id=actor,
        resource_type="workspace",
        resource_id=workspace_id,
        recipient_id=body.recipient_id,
    )


@endpoint(
    "/ownership-proposals/{proposal_id}/respond",
    methods=["POST"],
    auth=True,
    tags=["Workspaces"],
)
async def respond_ownership(request: Request, proposal_id: str) -> dict:
    """Accept or decline exactly one current proposal revision."""
    from app.services.ownership_consent import respond_ownership_transfer

    actor = _actor(request)
    body = await _body(request, OwnershipResponse)
    # Private recipient consent can be addressed from the Personal inbox. An
    # explicitly supplied scope remains syntactically/authoritatively checked.
    if request.headers.get("X-Integral-Scope") is not None:
        await resolve_workspace_id_from_request(request, actor)
    return await respond_ownership_transfer(
        actor_id=actor,
        proposal_id=proposal_id,
        revision=body.revision,
        action=body.action,
    )


@endpoint(
    "/workspaces/{workspace_id}/members/{member_id}/suspension",
    methods=["PUT"],
    auth=True,
    tags=["Workspaces"],
)
async def suspend_workspace_member(
    request: Request, workspace_id: str, member_id: str
) -> dict:
    """Retain or release a workspace block without recreating membership."""
    actor = _actor(request)
    await _scope(request, actor, workspace_id)
    from app.services.workspace_suspension import set_workspace_suspension

    body = await _body(request, SuspensionRequest)
    return await set_workspace_suspension(
        actor_id=actor,
        workspace_id=workspace_id,
        member_id=member_id,
        suspended=body.suspended,
        reason=body.reason,
        expected_revision=body.expected_revision,
        reviewed_access_digest=body.reviewed_access_digest,
    )


@endpoint(
    "/workspaces/{workspace_id}/rehome",
    methods=["POST"],
    auth=True,
    tags=["Workspaces"],
)
async def rehome_workspace(request: Request, workspace_id: str) -> dict:
    """Move canonical resources into an explicitly owner-controlled workspace."""
    from app.schemas.access_lifecycle import WorkspaceRehomeRequest
    from app.services.workspace_rehome import rehome_workspace_resources

    actor = _actor(request)
    await _scope(request, actor, workspace_id)
    body = await _body(request, WorkspaceRehomeRequest)
    return await rehome_workspace_resources(
        actor_id=actor,
        source_id=workspace_id,
        destination_id=body.destination_workspace_id,
    )


@endpoint(
    "/shares/{link_id}/grants/{recipient_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Shares"],
)
async def revoke_share_source(
    request: Request, link_id: str, recipient_id: str
) -> dict:
    """Revoke only this accepted source; report the remaining actual role."""
    from app.services.share_link_lifecycle import current_link, revoke_source_grant

    actor = _actor(request)
    if request.query_params:
        raise BadRequestError(message="Source revocation accepts path identities only")
    link = await current_link(link_id)
    await _scope(request, actor, link.workspace_id)
    return await revoke_source_grant(
        actor_id=actor, link_id=link_id, recipient_id=recipient_id
    )


@endpoint(
    "/workspaces/{workspace_id}/members/{member_id}/suspension",
    methods=["GET"],
    auth=True,
    tags=["Workspaces"],
)
async def review_member_suspension(
    request: Request, workspace_id: str, member_id: str
) -> dict:
    """Preview current retained authority before explicitly reviewed restoration."""
    from app.services.workspace_suspension import review_workspace_suspension

    actor = _actor(request)
    await _scope(request, actor, workspace_id)
    return await review_workspace_suspension(
        actor_id=actor, workspace_id=workspace_id, member_id=member_id
    )


@endpoint(
    "/access/{resource_type}/{resource_id}/blocks",
    methods=["PUT"],
    auth=True,
    tags=["Access"],
)
async def block_resource_subject(
    request: Request, resource_type: str, resource_id: str
) -> dict:
    """Apply an absolute identified subtree block under current manager admission."""
    from app.schemas.access_lifecycle import ResourceBlockRequest
    from app.services.request_scope import require_resource_effect_scope
    from app.services.resource_access_blocks import set_resource_block

    actor = _actor(request)
    if resource_type not in {"app", "track", "entry"}:
        raise BadRequestError(message="Invalid resource block target")
    await require_resource_effect_scope(request, actor, resource_type, resource_id)
    body = await _body(request, ResourceBlockRequest)
    return await set_resource_block(
        actor_id=actor,
        resource_type=resource_type,
        resource_id=resource_id,
        subject_id=body.subject_id,
        blocked=body.blocked,
        reason=body.reason,
    )


@endpoint("/me/ownership-proposals", methods=["GET"], auth=True, tags=["Workspaces"])
async def ownership_inbox(request: Request) -> dict:
    """Private consent inbox includes only the current recipient's proposals."""
    from app.services.ownership_consent import list_ownership_proposals

    actor = _actor(request)
    if request.headers.get("X-Integral-Scope") is not None:
        await resolve_workspace_id_from_request(request, actor)
    return await list_ownership_proposals(actor_id=actor)


@endpoint(
    "/workspaces/{workspace_id}/ownership-proposals",
    methods=["GET"],
    auth=True,
    tags=["Workspaces"],
)
async def workspace_ownership_inventory(request: Request, workspace_id: str) -> dict:
    """Owner inventory proves rooted targets before disclosing proposal facts."""
    from app.services.ownership_consent import list_ownership_proposals

    actor = _actor(request)
    await _scope(request, actor, workspace_id)
    return await list_ownership_proposals(actor_id=actor, workspace_id=workspace_id)
