"""Current account mailbox and server-owned proof used by invitation admission."""

from __future__ import annotations

import logging
import secrets
from typing import Optional

from app.models.nodes import User

logger = logging.getLogger(__name__)


async def current_account_email(user: User) -> Optional[str]:
    """Read the authoritative identity record, never a graph/profile email hint."""
    from jvspatial.api.auth.models import User as AuthUser

    from app.api.validators_common import validate_email

    if not user.user_id:
        return None
    try:
        identity = await AuthUser.get(user.user_id)
        return validate_email(identity.email) if identity else None
    except Exception:
        logger.warning("Cannot establish account mailbox for user=%s", user.id)
        return None


def email_proof_matches(user: User, email: str) -> bool:
    """Legacy booleans without a bound address cannot authorize email access."""
    verified = str(getattr(user, "verified_email", "") or "").strip().casefold()
    current = str(email or "").strip().casefold()
    return bool(
        user.email_verified and verified and current
    ) and secrets.compare_digest(verified.encode("utf-8"), current.encode("utf-8"))


async def verified_current_email(user: User) -> Optional[str]:
    """Return the current mailbox only when that exact address has been proved."""
    email = await current_account_email(user)
    return email if email and email_proof_matches(user, email) else None
