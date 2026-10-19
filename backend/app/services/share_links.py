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

import asyncio
import hashlib
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from app.models.edges import HAS_SHARE_LINK
from app.models.nodes import ShareLink
from app.services.permissions import resolve_role
from app.services.sharing import (
    _load_resource,
    _resource_workspace_id,
)
from app.services.sharing_write_scope import collaboration_mutation
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)
_REDEMPTION_LOCKS: Dict[tuple[int, int, str], asyncio.Lock] = {}

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


async def _reserve_redemption(link: ShareLink) -> bool:
    """Increment the redemption count only while the observed count still wins."""
    from jvspatial.db.database import Database

    graph_context = await link.get_context()
    database = graph_context.database
    concrete = database
    seen = set()
    while getattr(concrete, "inner", None) is not None and id(concrete) not in seen:
        seen.add(id(concrete))
        concrete = concrete.inner
    native_atomic = (
        type(concrete).find_one_and_update is not Database.find_one_and_update
    )
    lock = None
    if not native_atomic:
        if type(concrete).__module__ != "jvspatial.db.jsondb":
            return False
        lock = _REDEMPTION_LOCKS.setdefault(
            (id(concrete), id(asyncio.get_running_loop()), str(link.id)),
            asyncio.Lock(),
        )
    maximum = int(getattr(link, "max_redemptions", 0) or 0)
    for _attempt in range(10):
        previous = int(getattr(link, "redemptions", 0) or 0)
        if maximum > 0 and previous >= maximum:
            return False
        query = {
            "id": link.id,
            "context.redemptions": previous,
            "context.max_redemptions": maximum,
            # PostgreSQL distinguishes JSONB `null` from SQL NULL. Use the
            # JSON-value equality path so nullable fields match consistently
            # across supported database adapters.
            "context.revoked_at": {"$in": [getattr(link, "revoked_at", None)]},
            "context.expires_at": {"$in": [getattr(link, "expires_at", None)]},
        }
        update: Dict[str, Any] = {"$inc": {"context.redemptions": 1}}

        async def claim(query=query, update=update):
            return await database.find_one_and_update(
                graph_context._get_collection_name("n"), query, update
            )

        if lock is None:
            record = await claim()
        else:
            async with lock:
                record = await claim()
        if record is not None:
            link.redemptions = int(
                (record.get("context") or {}).get("redemptions") or 0
            )
            return True
        refreshed = await ShareLink.get(link.id)
        if refreshed is None:
            return False
        link.redemptions = int(getattr(refreshed, "redemptions", 0) or 0)
    return False


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


@collaboration_mutation
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
    from app.runtime.composition import require_capability

    require_capability("collaboration")
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
        if type(max_redemptions) is not int or max_redemptions < 0:
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
    from app.services.share_link_lifecycle import record_mint_event

    await record_mint_event(
        link=link,
        actor_id=actor_user_id,
        before=None,
        after={
            "share_link_id": link.id,
            "role": role,
            "intent": intent,
            "expires_at": expires_at,
        },
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
    """Disable future redemption; accepted sources require separate revocation."""
    from app.services.share_link_lifecycle import disable_source_link

    return await disable_source_link(actor_user_id, share_link_id)


async def redeem_share_link(actor_user_id: str, plaintext_token: str) -> Dict[str, Any]:
    """Redeem through the canonical atomic source-lifetime service."""
    from app.runtime.composition import require_capability

    require_capability("collaboration")
    from app.services.share_link_lifecycle import redeem_source_link

    return await redeem_source_link(actor_user_id, plaintext_token)


async def list_active_links(
    actor_user_id: str, resource_type: ResourceType, resource_id: str
) -> List[Dict[str, Any]]:
    """Owner-side listing of active links for a resource (no token shown)."""
    from app.api.errors import InsufficientPermissionsError

    role_on_resource = await resolve_role(actor_user_id, resource_type, resource_id)
    from app.services.workspace_governance import resolve_owner_governance

    if (
        role_on_resource not in ("owner", "admin")
        and await resolve_owner_governance(
            actor_user_id, resource_type, resource_id, "resource.access.review"
        )
        is None
    ):
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
