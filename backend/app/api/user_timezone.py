"""Self-only account time-zone settings, available to every authenticated user."""

from typing import Any

from fastapi import Request
from jvspatial.api import endpoint
from pydantic import ValidationError

from app.api.errors import (
    BadRequestError,
    MissingAuthenticationError,
    ResourceNotFoundError,
)
from app.api.utils import resolve_principal_id
from app.schemas.user_timezone import UserTimezoneUpdate
from app.services.speech_preferences import resolve_user
from app.services.user_timezone import set_user_timezone


@endpoint("/users/me/timezone", methods=["GET"], auth=True, tags=["Users"])
async def get_my_timezone(request: Request) -> dict[str, Any]:
    """Read the authenticated caller's saved or fallback time zone."""
    principal = resolve_principal_id(request)
    if not principal:
        raise MissingAuthenticationError(message="Authentication required")
    user = await resolve_user(principal)
    if user is None:
        raise ResourceNotFoundError(message="User not found")
    return {"timezone": user.timezone or "UTC", "configured": bool(user.timezone)}


@endpoint("/users/me/timezone", methods=["PATCH"], auth=True, tags=["Users"])
async def patch_my_timezone(request: Request) -> dict[str, Any]:
    """Save an explicit choice, or conditionally apply device detection."""
    principal = resolve_principal_id(request)
    if not principal:
        raise MissingAuthenticationError(message="Authentication required")
    try:
        patch = UserTimezoneUpdate.model_validate(await request.json())
    except (ValueError, ValidationError) as exc:
        raise BadRequestError(message="Provide a valid IANA time zone") from exc
    return await set_user_timezone(principal, patch)
