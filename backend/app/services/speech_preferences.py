"""Per-user dictation preferences stored on ``User.speech_preferences``."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from pydantic import ValidationError

from app.models.nodes import User
from app.schemas.speech_preferences import SpeechPreferences
from app.services.change_event import emit_change_event
from app.services.permissions import get_user_node
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


async def resolve_user(principal_id: str) -> Optional[User]:
    """Find the caller's User node by node id or auth-user id."""
    user = await get_user_node(principal_id)
    if user is None:
        user = await User.get(principal_id)
    return user


def preferences_of(user: Optional[User]) -> SpeechPreferences:
    """Return the user's stored preferences, or defaults.

    A stored dict that no longer validates (schema drift) falls back to the
    defaults rather than breaking the composer; the next PATCH rewrites it.
    """
    stored = getattr(user, "speech_preferences", None) if user else None
    if not stored:
        return SpeechPreferences()
    try:
        return SpeechPreferences.model_validate(stored)
    except ValidationError:
        logger.warning(
            "stored speech_preferences failed validation; using defaults",
            extra={"user_id": getattr(user, "id", None)},
        )
        return SpeechPreferences()


async def get_speech_preferences(principal_id: str) -> SpeechPreferences:
    """Resolve the principal's preferences (defaults when none are stored)."""
    return preferences_of(await resolve_user(principal_id))


def _describe(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "invalid preferences"
    first = errors[0]
    loc = ".".join(str(part) for part in first.get("loc", ()))
    msg = str(first.get("msg", "invalid value"))
    return f"{loc}: {msg}" if loc else msg


async def update_speech_preferences(
    *, user: User, actor_id: str, patch: Dict[str, Any]
) -> SpeechPreferences:
    """Merge ``patch`` over the stored preferences, validate, persist, audit.

    Raises ``ValueError`` for unknown fields or invalid values. Emits exactly
    one ``user.update`` ChangeEvent tagged ``section=speech_preferences``.
    """
    unknown = set(patch) - set(SpeechPreferences.model_fields)
    if unknown:
        raise ValueError(f"Unknown fields in body: {sorted(unknown)}")

    before = preferences_of(user)
    try:
        merged = SpeechPreferences.model_validate({**before.model_dump(), **patch})
    except ValidationError as exc:
        raise ValueError(_describe(exc)) from exc

    user.speech_preferences = merged.model_dump()
    user.updated_at = utc_now_iso()
    await user.save()

    await emit_change_event(
        actor_kind="human",
        actor_id=actor_id,
        action="user.update",
        resource_type="User",
        resource_id=user.id,
        before=before.model_dump(),
        after=merged.model_dump(),
        scope=f"user:{user.id}",
        details={"section": "speech_preferences"},
    )
    return merged
