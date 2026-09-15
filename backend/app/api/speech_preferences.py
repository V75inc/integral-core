"""Per-user dictation preferences — GET + PATCH ``/users/me/speech-preferences``.

PATCH merges a partial body over the stored preferences, validates the result
(``extra="forbid"``), and emits one ``user.update`` ChangeEvent tagged
``details.section == "speech_preferences"`` — see
``app/services/speech_preferences.py``.
"""

from typing import Any, Dict

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import (
    BadRequestError,
    MissingAuthenticationError,
    ResourceNotFoundError,
)
from app.api.utils import resolve_principal_id
from app.services.speech_preferences import (
    preferences_of,
    resolve_user,
    update_speech_preferences,
)


@endpoint(
    "/users/me/speech-preferences",
    methods=["GET"],
    auth=True,
    tags=["Users"],
)
async def get_my_speech_preferences(request: Request) -> Dict[str, Any]:
    """Return the caller's dictation preferences (defaults when unset)."""
    caller_id = resolve_principal_id(request)
    if not caller_id:
        raise MissingAuthenticationError(message="Authentication required")
    user = await resolve_user(caller_id)
    if user is None:
        raise ResourceNotFoundError(message="User not found")
    return preferences_of(user).model_dump()


@endpoint(
    "/users/me/speech-preferences",
    methods=["PATCH"],
    auth=True,
    tags=["Users"],
)
async def patch_my_speech_preferences(request: Request) -> Dict[str, Any]:
    """Partially update the caller's dictation preferences."""
    caller_id = resolve_principal_id(request)
    if not caller_id:
        raise MissingAuthenticationError(message="Authentication required")
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise BadRequestError(message="Invalid JSON body") from exc
    if not isinstance(body, dict):
        raise BadRequestError(message="Body must be a JSON object")

    user = await resolve_user(caller_id)
    if user is None:
        raise ResourceNotFoundError(message="User not found")
    try:
        prefs = await update_speech_preferences(
            user=user, actor_id=caller_id, patch=body
        )
    except ValueError as exc:
        raise BadRequestError(message=str(exc)) from exc
    return prefs.model_dump()
