"""Workspace admin and owner profile lifecycle; no model-supplied authority."""

from fastapi import Request
from jvspatial.api import endpoint

from app.agentive.services import profile_management as service
from app.agentive.services import public_web_chat
from app.api.errors import BadRequestError, MissingAuthenticationError
from app.api.utils import resolve_principal_id
from app.schemas.agentive.connection_profiles import (
    BindProfile,
    CreateProfile,
    NativeChatAllowlist,
    ProfileRevision,
    PublicWebChatAssignment,
    PublicWebChatEnablement,
    PublishProfile,
    RenameWorkspaceAssistant,
    SaveProfileDraft,
)
from app.services.permissions import get_user_node
from app.services.request_scope import resolve_workspace_id_from_request


async def scope(request):
    """Resolve and authorize the workspace profile manager."""
    user = await get_user_node(resolve_principal_id(request))
    if user is None:
        raise MissingAuthenticationError("Authentication required")
    workspace = await resolve_workspace_id_from_request(request, user.id)
    await service.require_profile_manager(user.id, workspace)
    return user.id, workspace


async def body(request, schema):
    """Parse a request body against the supplied profile schema."""
    try:
        return schema.model_validate(await request.json())
    except ValueError:
        raise BadRequestError("Invalid profile request") from None


@endpoint("/workspace/agent-profiles", methods=["GET"], auth=True, tags=["Agentive"])
async def get_profiles(request: Request) -> dict:
    """List the workspace profile settings and capabilities."""
    return await service.describe(*(await scope(request)))


@endpoint(
    "/workspace/agent-profiles/draft", methods=["PUT"], auth=True, tags=["Agentive"]
)
async def save_profile_draft(request: Request) -> dict:
    """Save a profile draft at the expected revision."""
    actor, workspace = await scope(request)
    return await service.save_draft(
        actor, workspace, await body(request, SaveProfileDraft)
    )


@endpoint(
    "/workspace/agent-profiles/test", methods=["POST"], auth=True, tags=["Agentive"]
)
async def test_profile_draft(request: Request) -> dict:
    """Test the profile draft without producing live effects."""
    actor, workspace = await scope(request)
    return await service.test_draft(
        actor, workspace, await body(request, ProfileRevision)
    )


@endpoint(
    "/workspace/agent-profiles/publish", methods=["POST"], auth=True, tags=["Agentive"]
)
async def publish_profile(request: Request) -> dict:
    """Publish a tested workspace profile."""
    actor, workspace = await scope(request)
    return await service.publish(actor, workspace, await body(request, PublishProfile))


@endpoint(
    "/workspace/agent-profiles/bind", methods=["POST"], auth=True, tags=["Agentive"]
)
async def bind_profile(request: Request) -> dict:
    """Assign an eligible connection to a published profile."""
    actor, workspace = await scope(request)
    return await service.bind(actor, workspace, await body(request, BindProfile))


@endpoint("/workspace/agent-profiles", methods=["POST"], auth=True, tags=["Agentive"])
async def create_profile(request: Request) -> dict:
    """Create a new workspace profile draft."""
    actor, workspace = await scope(request)
    return await service.create_profile(
        actor, workspace, await body(request, CreateProfile)
    )


@endpoint(
    "/workspace/agent-profiles/native-chat",
    methods=["PUT"],
    auth=True,
    tags=["Agentive"],
)
async def set_native_chat_profiles(request: Request) -> dict:
    """Set the published profiles offered in workspace chat."""
    actor, workspace = await scope(request)
    return await service.set_native_chat_profiles(
        actor, workspace, await body(request, NativeChatAllowlist)
    )


@endpoint(
    "/workspace/agent-profiles/workspace-assistant",
    methods=["PUT"],
    auth=True,
    tags=["Agentive"],
)
async def rename_workspace_assistant(request: Request) -> dict:
    """Update the default workspace assistant identity."""
    actor, workspace = await scope(request)
    return await service.rename_workspace_assistant(
        actor, workspace, await body(request, RenameWorkspaceAssistant)
    )


@endpoint(
    "/workspace/agent-profiles/public-web-chat",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def get_public_web_chat(request: Request) -> dict:
    """Return the Public Web Chat settings."""
    return await public_web_chat.describe(*(await scope(request)))


@endpoint(
    "/workspace/agent-profiles/public-web-chat/assignment",
    methods=["PUT"],
    auth=True,
    tags=["Agentive"],
)
async def assign_public_web_chat(request: Request) -> dict:
    """Set the published profile assigned to Public Web Chat."""
    actor, workspace = await scope(request)
    update = await body(request, PublicWebChatAssignment)
    await public_web_chat.assign_profile(
        actor, workspace, update.expected_revision, update.profile_key
    )
    return await public_web_chat.describe(actor, workspace)


@endpoint(
    "/workspace/agent-profiles/public-web-chat/enablement",
    methods=["PUT"],
    auth=True,
    tags=["Agentive"],
)
async def set_public_web_chat_enablement(request: Request) -> dict:
    """Set whether the ready Public Web Chat route is enabled."""
    actor, workspace = await scope(request)
    update = await body(request, PublicWebChatEnablement)
    ready = update.enabled and await public_web_chat.transport_ready(workspace)
    await public_web_chat.set_enabled(
        actor,
        workspace,
        update.expected_revision,
        update.enabled,
        transport_ready=ready,
    )
    return await public_web_chat.describe(actor, workspace)
