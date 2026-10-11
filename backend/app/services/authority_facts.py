"""Primary authority facts without hydration defaults or partial candidates."""

from typing import Any, Iterable

from jvspatial.api.auth.models import User as AuthUser

from app.exceptions import AuthorizationUnavailableError


def relationship_literal(row: dict, field: str, allowed: Iterable[str]) -> str:
    """Require one exact literal, rejecting conflicting legacy representations."""
    context = row.get("context")
    if context is not None and not isinstance(context, dict):
        raise AuthorizationUnavailableError()
    context = context or {}
    canonical = field in context
    legacy = field in row
    if not canonical and not legacy:
        raise AuthorizationUnavailableError()
    if canonical and legacy and context[field] != row[field]:
        raise AuthorizationUnavailableError()
    value = context[field] if canonical else row[field]
    if not isinstance(value, str) or value not in allowed:
        raise AuthorizationUnavailableError()
    return value


def relationship_flag(row: dict, field: str) -> bool:
    """Absent optional creation grants deny; present grants require actual bools."""
    context = row.get("context")
    if context is not None and not isinstance(context, dict):
        raise AuthorizationUnavailableError()
    context = context or {}
    canonical = field in context
    legacy = field in row
    if (
        canonical
        and legacy
        and (
            type(context[field]) is not type(row[field]) or context[field] != row[field]
        )
    ):
        raise AuthorizationUnavailableError()
    value = context[field] if canonical else row.get(field, False)
    if not isinstance(value, bool):
        raise AuthorizationUnavailableError()
    return value


async def primary_auth_record(database: Any, identity_id: str) -> dict | None:
    """Read one identity with an explicit lifecycle flag from the primary store."""
    try:
        rows = await database.find(
            "object", {"id": identity_id, "entity": AuthUser._entity_name()}, limit=2
        )
        if not rows:
            return None
        if len(rows) != 1:
            raise AuthorizationUnavailableError()
        if (
            rows[0].get("id") != identity_id
            or rows[0].get("entity") != AuthUser._entity_name()
        ):
            raise AuthorizationUnavailableError()
        context = rows[0].get("context")
        if not isinstance(context, dict) or not isinstance(
            context.get("is_active"), bool
        ):
            return None
        return rows[0]
    except Exception as exc:
        raise AuthorizationUnavailableError() from exc
