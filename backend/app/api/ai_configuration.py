"""Authenticated BYOK connection and model-profile configuration."""

from __future__ import annotations

import os
from typing import Any

from fastapi import Request
from jvspatial.api import endpoint
from pydantic import ValidationError

from app.api.errors import BadRequestError
from app.api.utils import resolve_principal_id
from app.schemas.ai_configuration import (
    CapabilitiesRequest,
    ConnectionWrite,
    ItemRequest,
    ProfileWrite,
)
from app.services import ai_configuration as service
from app.services.change_event import emit_change_event
from app.services.model_settings import model_capabilities


async def _body(request: Request, schema):
    try:
        return schema.model_validate(await request.json())
    except (ValueError, ValidationError):
        # Input may contain a secret; never reflect validation input.
        raise BadRequestError(
            "Invalid AI configuration. Check the selected fields."
        ) from None


async def _mutate(request: Request, operation, *, action: str) -> Any:
    user_id = resolve_principal_id(request)
    try:
        result = await operation(user_id)
    except (ValueError, RuntimeError) as exc:
        raise BadRequestError(str(exc)) from None
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action=action,
        resource_type="AIConfiguration",
        resource_id=user_id,
        scope=user_id,
        before=None,
        after={"action": action},
    )
    return result or {"ok": True}


@endpoint("/users/me/ai-configuration", methods=["GET"], auth=True, tags=["AI Models"])
async def get_configuration(request: Request) -> dict[str, Any]:
    """Describe personal profiles and the effective workspace funding configuration."""
    from app.config import settings
    from app.services.model_credentials import get_active_credential_for_user
    from app.services.permissions import get_user_node
    from app.services.request_scope import resolve_workspace_id_from_request
    from app.services.workspace_permissions import get_workspace_owner_user_id

    user_id = resolve_principal_id(request)
    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    owner_id = await get_workspace_owner_user_id(workspace_id)
    owner = await get_user_node(owner_id) if owner_id else None
    owner_subject = owner.user_id if owner else None
    record = (
        await get_active_credential_for_user(owner_subject) if owner_subject else None
    )
    if record and record.active_profile_id:
        record = await service.profile_credential(
            owner_subject, record.active_profile_id, record
        )
    personal = settings.INTEGRAL_AGENT_KEY_MODE != "platform_only" and bool(record)
    result = await service.configuration(user_id)
    result["effective"] = {
        "workspace_id": workspace_id,
        "source": (
            "workspace_owner"
            if personal
            else (
                "unavailable"
                if settings.INTEGRAL_AGENT_KEY_MODE == "byo_strict"
                else "deployment"
            )
        ),
        "editable": owner_subject == user_id
        and settings.INTEGRAL_AGENT_KEY_MODE != "platform_only",
        "provider": record.provider if personal else "",
        "model": (
            record.model
            if personal
            else (
                ""
                if settings.INTEGRAL_AGENT_KEY_MODE == "byo_strict"
                else os.getenv("INTEGRAL_NATIVE_MODEL", "")
            )
        ),
        "generation_settings": record.generation_settings if personal else {},
    }
    return result


@endpoint(
    "/users/me/model-capabilities", methods=["POST"], auth=True, tags=["AI Models"]
)
async def capabilities(request: Request) -> dict[str, Any]:
    """Return model parameter support without making an inference request."""
    body = await _body(request, CapabilitiesRequest)
    return model_capabilities(body.provider, body.model)


@endpoint(
    "/users/me/model-connections", methods=["POST"], auth=True, tags=["AI Models"]
)
async def save_connection(request: Request) -> dict[str, Any]:
    """Validate and save an owned provider connection without exposing its secret."""
    body = await _body(request, ConnectionWrite)
    return await _mutate(
        request,
        lambda user: service.save_connection(user, body),
        action="ai_connection.save",
    )


@endpoint(
    "/users/me/model-connections/test", methods=["POST"], auth=True, tags=["AI Models"]
)
async def test_connection(request: Request) -> dict[str, Any]:
    """Test the owned saved connection using its server-side secret."""
    body = await _body(request, ItemRequest)
    return await _mutate(
        request,
        lambda user: service.test_connection(user, body.id),
        action="ai_connection.test",
    )


@endpoint(
    "/users/me/model-connections/remove",
    methods=["POST"],
    auth=True,
    tags=["AI Models"],
)
async def remove_connection(request: Request) -> dict[str, Any]:
    """Remove an unused provider connection owned by the caller."""
    body = await _body(request, ItemRequest)
    return await _mutate(
        request,
        lambda user: service.remove_item(user, body.id, connection=True),
        action="ai_connection.remove",
    )


@endpoint("/users/me/model-profiles", methods=["POST"], auth=True, tags=["AI Models"])
async def save_profile(request: Request) -> dict[str, Any]:
    """Validate and save model options against the owned provider connection."""
    body = await _body(request, ProfileWrite)
    return await _mutate(
        request, lambda user: service.save_profile(user, body), action="ai_profile.save"
    )


@endpoint(
    "/users/me/model-profiles/activate", methods=["POST"], auth=True, tags=["AI Models"]
)
async def activate_profile(request: Request) -> dict[str, Any]:
    """Select an owned default profile while preserving voice credentials."""
    body = await _body(request, ItemRequest)
    return await _mutate(
        request,
        lambda user: service.activate_profile(user, body.id),
        action="ai_profile.activate",
    )


@endpoint(
    "/users/me/model-profiles/remove", methods=["POST"], auth=True, tags=["AI Models"]
)
async def remove_profile(request: Request) -> dict[str, Any]:
    """Remove an unused model profile owned by the caller."""
    body = await _body(request, ItemRequest)
    return await _mutate(
        request,
        lambda user: service.remove_item(user, body.id, connection=False),
        action="ai_profile.remove",
    )


@endpoint(
    "/users/me/conversation-model", methods=["POST"], auth=True, tags=["AI Models"]
)
async def conversation_model(request: Request) -> dict[str, Any]:
    """Read or change an owned idle conversation model selection."""
    from app.schemas.ai_configuration import ConversationModelRequest

    body = await _body(request, ConversationModelRequest)
    if body.profile_id is None:
        try:
            return await service.conversation_model(
                resolve_principal_id(request), body.thread_id
            )
        except ValueError as exc:
            raise BadRequestError(str(exc)) from None
    return await _mutate(
        request,
        lambda user: service.conversation_model(user, body.thread_id, body.profile_id),
        action="ai_conversation_model.configure",
    )
