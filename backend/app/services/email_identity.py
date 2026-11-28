"""Primary mailbox identity and exact server-owned proof for admission."""

from __future__ import annotations

import secrets
from typing import Optional

from jvspatial.api.auth.models import User as AuthUser
from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError, BadRequestError
from app.models.nodes import User
from app.services.authority_facts import (
    primary_auth_record,
    primary_node_record,
    required_node_text,
)

_ROSTER_BATCH = 500


async def current_account_email(user: User) -> Optional[str]:
    """Read the current active identity, independent of cached auth hydration."""
    from app.api.validators_common import validate_email

    if not user.user_id:
        return None
    row = await primary_auth_record(get_default_context().database, user.user_id)
    if row is None or row["context"].get("is_active") is not True:
        return None
    return validate_email(required_node_text(row, "email"))


async def roster_account_emails(users: list[User]) -> dict[str, str]:
    """Display addresses for active accounts, keyed by identity id.

    Reads the primary store in bounded batches. An account that is missing,
    inactive or holds no usable address is simply left out; a datastore failure
    or a duplicated identity still fails closed.
    """
    from app.api.validators_common import validate_email

    database = get_default_context().database
    wanted = sorted({user.user_id for user in users if user.user_id})
    emails: dict[str, str] = {}
    for start in range(0, len(wanted), _ROSTER_BATCH):
        batch = wanted[start : start + _ROSTER_BATCH]
        try:
            rows = await database.find(
                "object",
                {"id": {"$in": batch}, "entity": AuthUser._entity_name()},
                limit=len(batch) + 1,
            )
        except Exception as exc:
            raise AuthorizationUnavailableError() from exc
        ids = [row.get("id") for row in rows]
        if len(set(ids)) != len(ids) or not set(ids) <= set(batch):
            raise AuthorizationUnavailableError()
        for row in rows:
            context = row.get("context")
            if row.get("entity") != AuthUser._entity_name():
                raise AuthorizationUnavailableError()
            if not isinstance(context, dict) or context.get("is_active") is not True:
                continue
            try:
                emails[row["id"]] = validate_email(required_node_text(row, "email"))
            except (AuthorizationUnavailableError, BadRequestError):
                continue
    return emails


def email_proof_matches(user: User, email: str) -> bool:
    """Pure comparison; callers needing admission use verified_current_email."""
    if type(user.email_verified) is not bool or user.email_verified is not True:
        return False
    if not isinstance(user.verified_email, str) or not isinstance(email, str):
        return False
    verified = user.verified_email.strip().casefold()
    current = email.strip().casefold()
    return bool(verified and current) and secrets.compare_digest(
        verified.encode(), current.encode()
    )


async def _read_verified_current_email(user: User) -> Optional[str]:
    """Admit exact current primary proof; cached/coerced flags never grant."""
    database = get_default_context().database
    row = await primary_node_record(database, User, user.id)
    if row is None:
        return None
    auth_id = required_node_text(row, "user_id", allow_empty=True)
    if not auth_id or auth_id != user.user_id:
        return None
    profiles = await database.find(
        "node",
        {
            "entity": User._entity_name(),
            "$or": [{"context.user_id": auth_id}, {"user_id": auth_id}],
        },
        limit=2,
    )
    if len(profiles) != 1 or profiles[0].get("id") != user.id:
        raise AuthorizationUnavailableError()
    flag = row["context"].get("email_verified")
    if type(flag) is not bool or (
        "email_verified" in row
        and (type(row["email_verified"]) is not bool or row["email_verified"] != flag)
    ):
        raise AuthorizationUnavailableError()
    if not flag:
        return None
    proved = required_node_text(row, "verified_email", allow_empty=True)
    email = await current_account_email(user)
    if not email or not proved:
        return None
    return (
        email
        if secrets.compare_digest(proved.casefold().encode(), email.casefold().encode())
        else None
    )


async def verified_current_email(user: User) -> Optional[str]:
    """Fail closed with an explicit unavailable state on uncertain proof reads."""
    try:
        return await _read_verified_current_email(user)
    except AuthorizationUnavailableError:
        raise
    except Exception as exc:
        raise AuthorizationUnavailableError() from exc
