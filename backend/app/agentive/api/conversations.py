"""Conversation context CRUD endpoints."""

from typing import Any, Dict, List, Optional

from fastapi import Request
from jvspatial.api import endpoint

from app.agentive.services.conversation_context import (
    get_or_create_conversation_context,
    update_conversation_context,
)
from app.api.errors import (
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceNotFoundError,
)
from app.api.utils import export_node, resolve_principal_id


def _require_context_owner(ctx: Any, user_id: str) -> None:
    """Fail closed unless ``ctx.user_id`` matches the authenticated principal."""
    owner_id = getattr(ctx, "user_id", None) or ""
    if owner_id != user_id:
        raise InsufficientPermissionsError(
            message="Conversation context does not belong to the caller"
        )


@endpoint(
    "/agentive/conversations/context",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def create_conversation_context(
    request: Request,
    agent_type: str = "",
    agent_conversation_id: str = "",
    scope: str = "personal",
    persona: str = "",
    workspace_id: Optional[str] = None,
    agent_config_id: Optional[str] = None,
    parent_context_id: Optional[str] = None,
    focused_track_id: Optional[str] = None,
    focused_space_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create or get an Integral ConversationContext for an agent conversation."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    ctx = await get_or_create_conversation_context(
        user_id=user_id,
        agent_type=agent_type,
        agent_conversation_id=agent_conversation_id,
        scope=scope,
        persona=persona,
        workspace_id=workspace_id,
        agent_config_id=agent_config_id,
        parent_context_id=parent_context_id,
    )

    if focused_track_id or focused_space_id:
        ctx = await update_conversation_context(
            context_id=ctx.id,
            focused_track_id=focused_track_id,
            focused_space_id=focused_space_id,
        )

    return {"context": await export_node(ctx)}


@endpoint(
    "/agentive/conversations/context/{context_id}",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def get_conversation_context(request: Request, context_id: str) -> Dict[str, Any]:
    """Get a ConversationContext by ID."""
    from app.agentive.nodes import ConversationContext

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    ctx = await ConversationContext.get(context_id)
    if not ctx:
        raise ResourceNotFoundError(message="Context not found")

    _require_context_owner(ctx, user_id)

    return {"context": await export_node(ctx)}


@endpoint(
    "/agentive/conversations/context/{context_id}",
    methods=["PATCH"],
    auth=True,
    tags=["Agentive"],
)
async def patch_conversation_context(
    request: Request,
    context_id: str,
    focused_track_id: Optional[str] = None,
    focused_space_id: Optional[str] = None,
    entities_referenced: Optional[List[Any]] = None,
    user_patterns: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Update a ConversationContext's focus and references."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    from app.agentive.nodes import ConversationContext

    existing = await ConversationContext.get(context_id)
    if not existing:
        raise ResourceNotFoundError(message="Context not found")
    _require_context_owner(existing, user_id)

    ctx = await update_conversation_context(
        context_id=context_id,
        focused_track_id=focused_track_id,
        focused_space_id=focused_space_id,
        entities_referenced=entities_referenced,
        user_patterns=user_patterns,
    )
    if not ctx:
        raise ResourceNotFoundError(message="Context not found")

    return {
        "context": await export_node(ctx),
        "message": "Context updated",
    }


@endpoint(
    "/agentive/conversations/context/{context_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Agentive"],
)
async def delete_conversation_context(
    request: Request, context_id: str
) -> Dict[str, Any]:
    """Delete a ConversationContext."""
    from app.agentive.nodes import ConversationContext

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    ctx = await ConversationContext.get(context_id)
    if not ctx:
        raise ResourceNotFoundError(message="Context not found")

    _require_context_owner(ctx, user_id)

    await ctx.delete()
    return {"message": "Context deleted", "context_id": context_id}
