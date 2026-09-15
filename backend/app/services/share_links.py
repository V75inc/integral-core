"""Phase 4 — signed-in tokenized share-link primitives.

Owner mints a link with role + optional expiry. The plaintext token is
returned once; only the SHA-256 hash is persisted in ``ShareLink.token_hash``.

Redeeming the link materializes ``COLLABORATES_ON{role}`` from User → the
target resource and (cross-workspace) ``IS_MEMBER_OF{role:"guest"}``. The
flow is idempotent: re-opening the link when already a collaborator is a
no-op (no edge dupe, no role downgrade).

Anonymous access is intentionally NOT supported for collaborator links —
redemption requires an authenticated principal. Public read uses
``intent="public"`` and ``GET /api/public-share/{token}`` only.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, cast

from app.models.edges import COLLABORATES_ON, HAS_SHARE_LINK
from app.models.nodes import ShareLink
from app.schemas.audit import ChangeEventAction
from app.services.change_event import emit_change_event
from app.services.permissions import get_user_node, resolve_role
from app.services.sharing import (
    _emit_share_notification,
    _load_resource,
    _resource_label,
    _resource_workspace_id,
    ensure_guest_membership,
)
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

ResourceType = Literal["app", "track", "entry"]
ShareLinkIntent = Literal["collaborator", "public"]

# Collaborator share links must not grant owner via token redeem.
SHARE_LINK_MINT_ROLES: tuple[str, ...] = (
    "admin",
    "editor",
    "commenter",
    "viewer",
)


def _generate_token() -> str:
    """32-byte URL-safe plaintext token. Returned to issuer only once."""
    return secrets.token_urlsafe(32)


def _hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return utc_now_iso()


def link_intent(link: ShareLink) -> ShareLinkIntent:
    """Resolve intent; legacy rows without the field are collaborator-only."""
    raw = (getattr(link, "intent", None) or "").strip().lower()
    if raw == "public":
        return "public"
    return "collaborator"


def validate_expires_at(expires_at: Optional[str]) -> Optional[str]:
    """Return normalized ISO string or raise ``BadRequestError``."""
    from app.api.errors import BadRequestError

    if expires_at is None or expires_at == "":
        return None
    try:
        parsed = datetime.fromisoformat(str(expires_at))
    except (TypeError, ValueError) as exc:
        raise BadRequestError(
            message="expires_at must be a valid ISO-8601 datetime."
        ) from exc
    return parsed.isoformat()


def _is_expired(link: ShareLink) -> bool:
    if not link.expires_at:
        return False
    try:
        ts = datetime.fromisoformat(link.expires_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True
    return ts <= datetime.now(timezone.utc)


def _is_revoked(link: ShareLink) -> bool:
    return bool(link.revoked_at)


async def _wire_share_link_to_resource(
    *,
    resource: Any,
    link: ShareLink,
    resource_type: ResourceType,
    resource_id: str,
    attached_at: str,
) -> None:
    """Wire HAS_SHARE_LINK or roll back the ShareLink node (I-GRAPH-01)."""
    from app.api.errors import BadRequestError
    from app.services.edge_upsert import ensure_edge

    try:
        await ensure_edge(resource, link, HAS_SHARE_LINK, attached_at=attached_at)
    except Exception as exc:
        logger.exception(
            "mint_share_link: HAS_SHARE_LINK wire failed for resource=%s:%s link=%s",
            resource_type,
            resource_id,
            link.id,
        )
        try:
            await link.delete()
        except Exception:
            logger.exception(
                "mint_share_link: rollback delete failed for link=%s", link.id
            )
        raise BadRequestError(
            message="Could not attach share link to resource."
        ) from exc


async def mint_share_link(
    actor_user_id: str,
    resource_type: ResourceType,
    resource_id: str,
    role: str = "viewer",
    expires_at: Optional[str] = None,
    *,
    intent: ShareLinkIntent = "collaborator",
    max_redemptions: Optional[int] = None,
) -> Dict[str, Any]:
    """Owner or admin. Return ``{share_link, token}`` — token shown only once."""
    from app.api.errors import (
        BadRequestError,
        InsufficientPermissionsError,
        ResourceNotFoundError,
    )

    if intent not in ("collaborator", "public"):
        raise BadRequestError(message="intent must be 'collaborator' or 'public'.")
    if role not in SHARE_LINK_MINT_ROLES:
        raise BadRequestError(message=f"Role must be one of {SHARE_LINK_MINT_ROLES}.")
    expires_at = validate_expires_at(expires_at)

    # Full Sweep S4: collaborator links default to a finite redemption cap.
    # Public links stay unlimited (0) — they are view tokens, not grants.
    if max_redemptions is None:
        cap = 25 if intent == "collaborator" else 0
    else:
        if max_redemptions < 0:
            raise BadRequestError(message="max_redemptions must be >= 0.")
        cap = max_redemptions

    from app.schemas.policy import Resource, Subject
    from app.services.policy_engine import evaluate as policy_evaluate

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=actor_user_id),
        action=f"{resource_type}.share_link.mint",
        resource=Resource(
            kind=resource_type,
            id=resource_id,
            scope=f"{resource_type}:{resource_id}",
        ),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(
            message="Only the resource owner or admin may mint share links."
        )
    resource = await _load_resource(resource_type, resource_id)
    if resource is None:
        raise ResourceNotFoundError(message=f"{resource_type.capitalize()} not found.")
    workspace_id = await _resource_workspace_id(resource_type, resource) or ""

    plaintext = _generate_token()
    now_iso = _now_iso()
    link = await ShareLink.create(
        resource_type=resource_type,
        resource_id=resource_id,
        workspace_id=workspace_id,
        role=role,
        intent=intent,
        token_hash=_hash_token(plaintext),
        created_by=actor_user_id,
        created_at=now_iso,
        expires_at=expires_at,
        revoked_at=None,
        redemptions=0,
        max_redemptions=cap,
    )
    await _wire_share_link_to_resource(
        resource=resource,
        link=link,
        resource_type=resource_type,
        resource_id=resource_id,
        attached_at=now_iso,
    )
    await emit_change_event(
        actor_kind="human",
        actor_id=actor_user_id,
        action=cast(ChangeEventAction, f"{resource_type}.share_link.mint"),
        resource_type=resource_type.capitalize(),
        resource_id=resource_id,
        before=None,
        after={
            "share_link_id": link.id,
            "role": role,
            "intent": intent,
            "expires_at": expires_at,
        },
        scope=f"{resource_type}:{resource_id}",
    )
    return {
        "share_link": {
            "id": link.id,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "role": role,
            "intent": intent,
            "created_at": link.created_at,
            "expires_at": link.expires_at,
            "revoked_at": link.revoked_at,
            "redemptions": link.redemptions,
        },
        "token": plaintext,  # one-time return
    }


async def revoke_share_link(actor_user_id: str, share_link_id: str) -> Dict[str, Any]:
    """Mark a link as revoked. Subsequent redeem attempts return 410."""
    from app.api.errors import InsufficientPermissionsError, ResourceNotFoundError

    link = await ShareLink.get(share_link_id)
    if not link:
        raise ResourceNotFoundError(message="Share link not found.")
    from app.schemas.policy import Resource, Subject
    from app.services.policy_engine import evaluate as policy_evaluate

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=actor_user_id),
        action=f"{link.resource_type}.share_link.revoke",
        resource=Resource(
            kind=link.resource_type,
            id=link.resource_id,
            scope=f"{link.resource_type}:{link.resource_id}",
        ),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(
            message="Only the resource owner or admin may revoke share links."
        )
    if not link.revoked_at:
        link.revoked_at = _now_iso()
        await link.save()
    await emit_change_event(
        actor_kind="human",
        actor_id=actor_user_id,
        action=cast(ChangeEventAction, f"{link.resource_type}.share_link.revoke"),
        resource_type=link.resource_type.capitalize(),
        resource_id=link.resource_id,
        before=None,
        after={"share_link_id": link.id},
        scope=f"{link.resource_type}:{link.resource_id}",
    )
    return {"share_link_id": link.id, "revoked_at": link.revoked_at}


async def redeem_share_link(actor_user_id: str, plaintext_token: str) -> Dict[str, Any]:
    """Signed-in redeem. Materializes COLLABORATES_ON + guest IS_MEMBER_OF."""
    from app.api.errors import (
        BadRequestError,
        InsufficientPermissionsError,
        ResourceNotFoundError,
    )

    if not actor_user_id:
        raise InsufficientPermissionsError(message="Authentication required.")
    if not plaintext_token:
        raise BadRequestError(message="Token is required.")

    from app.schemas.policy import Resource, Subject
    from app.services.policy_engine import evaluate as policy_evaluate

    token_hash = _hash_token(plaintext_token)
    matches = await ShareLink.find({"context.token_hash": token_hash})
    if not matches:
        raise ResourceNotFoundError(message="Invalid or revoked link.")
    link = matches[0]
    if not secrets.compare_digest(str(link.token_hash or ""), token_hash):
        raise ResourceNotFoundError(message="Invalid or revoked link.")
    if link_intent(link) == "public":
        raise BadRequestError(
            message="This link is for public viewing; sign in is not required."
        )
    redeem_decision = await policy_evaluate(
        subject=Subject(kind="human", id=actor_user_id),
        action=f"{link.resource_type}.share_link.redeem",
        resource=Resource(
            kind=link.resource_type,
            id=link.resource_id,
            scope=f"{link.resource_type}:{link.resource_id}",
        ),
    )
    if not redeem_decision.allowed:
        raise InsufficientPermissionsError(message="Access denied.")

    if _is_revoked(link):
        raise BadRequestError(message="This share link has been revoked.")
    if _is_expired(link):
        raise BadRequestError(message="This share link has expired.")
    max_r = int(getattr(link, "max_redemptions", 0) or 0)
    if max_r > 0 and int(getattr(link, "redemptions", 0) or 0) >= max_r:
        raise BadRequestError(
            message="This share link has reached its redemption limit."
        )

    user = await get_user_node(actor_user_id)
    if user is None:
        raise ResourceNotFoundError(message="User not found.")
    resource = await _load_resource(link.resource_type, link.resource_id)
    if resource is None:
        raise ResourceNotFoundError(
            message=f"Linked {link.resource_type} no longer exists."
        )

    # Cross-workspace guest membership.
    if link.workspace_id:
        await ensure_guest_membership(
            user,
            link.workspace_id,
            inviter_user_id=link.created_by,
            source_label=f"share_link:{link.id}",
        )

    # Materialize COLLABORATES_ON if not already present.
    current = await resolve_role(actor_user_id, link.resource_type, link.resource_id)
    created_edge = False
    if current is None:
        from app.services.edge_upsert import ensure_edge, find_existing_edge

        existing = await find_existing_edge(user, resource, COLLABORATES_ON)
        await ensure_edge(
            user,
            resource,
            COLLABORATES_ON,
            role=link.role,
            invited_at=_now_iso(),
            invited_by=link.created_by or "system:share-link",
        )
        created_edge = existing is None
        label = await _resource_label(link.resource_type, resource)
        await _emit_share_notification(
            recipient_user_id=user.id,
            kind="share.link_redeemed",
            title=f"You joined {label}",
            body=f"Role: {link.role}",
            resource_type=link.resource_type,  # type: ignore[arg-type]
            resource_id=link.resource_id,
        )

    link.redemptions = (link.redemptions or 0) + 1
    await link.save()

    await emit_change_event(
        actor_kind="human",
        actor_id=actor_user_id,
        action=cast(ChangeEventAction, f"{link.resource_type}.share_link.redeem"),
        resource_type=link.resource_type.capitalize(),
        resource_id=link.resource_id,
        before=None,
        after={
            "share_link_id": link.id,
            "role": link.role,
            "created_edge": created_edge,
        },
        scope=f"{link.resource_type}:{link.resource_id}",
    )

    return {
        "resource_type": link.resource_type,
        "resource_id": link.resource_id,
        "role": link.role,
        "created_edge": created_edge,
        "redirect_to": f"/{link.resource_type}s/{link.resource_id}",
    }


async def list_active_links(
    actor_user_id: str, resource_type: ResourceType, resource_id: str
) -> List[Dict[str, Any]]:
    """Owner-side listing of active links for a resource (no token shown)."""
    from app.api.errors import InsufficientPermissionsError

    role_on_resource = await resolve_role(actor_user_id, resource_type, resource_id)
    if role_on_resource not in ("owner", "admin"):
        raise InsufficientPermissionsError(
            message="Only the resource owner or admin may list share links."
        )
    links = await ShareLink.find(
        {
            "context.resource_type": resource_type,
            "context.resource_id": resource_id,
        }
    )
    out: List[Dict[str, Any]] = []
    for link in links:
        if _is_revoked(link) or _is_expired(link):
            continue
        out.append(
            {
                "id": link.id,
                "role": link.role,
                "intent": link_intent(link),
                "created_at": link.created_at,
                "created_by": link.created_by,
                "expires_at": link.expires_at,
                "redemptions": link.redemptions or 0,
            }
        )
    return out
