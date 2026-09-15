"""Generic public-share endpoints (DR-30-02 + DR-30-04).

POST /api/entries/{id}/public-share # mint
GET /api/public-share/{token} # redeem (unauthenticated)
GET /api/portfolio/shared/{token} # adapter route (URL stability)

Replaces legacy-era /api/portfolio/{id}/shares + /api/portfolio/shared/{token}.
All projection semantics live in bundle hooks[] declarative blocks.
"""

from __future__ import annotations

import secrets
from typing import Any, Dict, Optional

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import ResourceNotFoundError
from app.api.utils import resolve_principal_id
from app.models.nodes import Entry, EntryType, ShareLink, Track
from app.schemas.hooks.public_share import (
    MintPublicShareRequest,
    MintPublicShareResponse,
    PublicShareReadResponse,
)
from app.services.hooks.declarative import (
    ShareGateDeniedError,
    apply_public_share_projection,
)
from app.services.hooks.errors import (
    AmbiguousHookError,
    HookNotConfiguredError,
)
from app.services.hooks.resolver import find_matching_bindings
from app.services.share_links import (
    _hash_token,
    _is_expired,
    link_intent,
    mint_share_link,
)


def _slug(name: str) -> str:
    return (name or "").strip().lower().replace(" ", "_").replace("-", "_")


async def _resolve_binding(entry: Entry, hook_key: Optional[str]) -> Dict[str, Any]:
    et = await EntryType.get(entry.type_id) if entry.type_id else None
    entry_type_key = _slug(et.name if et else "")
    track = await Track.get(entry.track_id) if entry.track_id else None
    workspace_id = track.workspace_id if track else ""
    payload = {"entry_type": entry_type_key}
    bindings = find_matching_bindings(
        workspace_id, "entry.public_share", payload, explicit_hook_key=hook_key
    )
    if not bindings:
        raise HookNotConfiguredError(
            message="no entry.public_share hook matched",
            details={"payload": payload},
        )
    if len(bindings) > 1 and not hook_key:
        raise AmbiguousHookError(
            message="multiple bindings matched; supply hook_key",
            details={"candidates": [b["key"] for b in bindings]},
        )
    return bindings[0]


@endpoint(
    "/entries/{entry_id}/public-share", methods=["POST"], auth=True, tags=["Entries"]
)
async def mint_public_share(request: Request, entry_id: str) -> Dict[str, Any]:
    """Mint a public share-link token for an entry, gated by the matching public_share hook."""
    user_id = resolve_principal_id(request)
    raw = await request.json() if request.method == "POST" else {}
    req = MintPublicShareRequest.model_validate(raw or {})

    entry = await Entry.get(entry_id)
    if entry is None:
        # Existence-hiding: refuse with the same shape regardless of cause.
        raise ResourceNotFoundError(message="Entry not eligible for public share")
    try:
        binding = await _resolve_binding(entry, req.hook_key)
    except HookNotConfiguredError:
        raise ResourceNotFoundError(message="Entry not eligible for public share")

    # Probe the gate before minting — refuse upfront if gate denies.
    entry_payload = {
        "title": entry.title,
        "body": entry.body,
        "custom_fields": dict(entry.custom_fields or {}),
    }
    try:
        apply_public_share_projection(entry_payload, binding.get("declarative") or {})
    except ShareGateDeniedError:
        raise ResourceNotFoundError(message="Entry not eligible for public share")

    result = await mint_share_link(
        actor_user_id=user_id,
        resource_type="entry",
        resource_id=entry_id,
        role="viewer",
        expires_at=req.expires_at,
        intent="public",
    )
    return MintPublicShareResponse(
        token=result["token"],
        share_link_id=result["share_link"]["id"],
        hook_key=binding["key"],
    ).model_dump()


async def _redeem(token: str) -> Dict[str, Any]:
    if not token or len(token) < 16:
        raise ResourceNotFoundError(message="Shared entry not found")
    token_hash = _hash_token(token)
    matches = await ShareLink.find({"context.token_hash": token_hash})
    link = None
    for candidate in matches:
        if secrets.compare_digest(str(candidate.token_hash or ""), token_hash):
            link = candidate
            break
    if link is None or link.resource_type != "entry":
        raise ResourceNotFoundError(message="Shared entry not found")
    if link_intent(link) != "public":
        raise ResourceNotFoundError(message="Shared entry not found")
    if link.revoked_at or _is_expired(link):
        raise ResourceNotFoundError(message="Shared entry not found")
    entry = await Entry.get(link.resource_id)
    if entry is None:
        raise ResourceNotFoundError(message="Shared entry not found")
    try:
        binding = await _resolve_binding(entry, hook_key=None)
    except (HookNotConfiguredError, AmbiguousHookError):
        raise ResourceNotFoundError(message="Shared entry not found")
    entry_payload = {
        "title": entry.title,
        "body": entry.body,
        "custom_fields": dict(entry.custom_fields or {}),
    }
    try:
        projection = apply_public_share_projection(
            entry_payload, binding.get("declarative") or {}
        )
    except ShareGateDeniedError:
        raise ResourceNotFoundError(message="Shared entry not found")
    return PublicShareReadResponse(
        hook_key=binding["key"],
        projection=projection,
        shared_at=link.created_at or "",
    ).model_dump()


@endpoint("/public-share/{token}", methods=["GET"], auth=False, tags=["Entries"])
async def get_public_share(request: Request, token: str) -> Dict[str, Any]:
    """Redeem a public-share token and return the bundle-projected entry payload."""
    return await _redeem(token)


@endpoint("/portfolio/shared/{token}", methods=["GET"], auth=False, tags=["Entries"])
async def get_public_share_legacy_alias(request: Request, token: str) -> Dict[str, Any]:
    """Phase 21 URL stability adapter (DR-30-04).

    Frontend SharedPortfolioPage uses /api/portfolio/shared/{token}.
    Maintains external URL while internals route through generic
    public-share dispatcher.
    """
    return await _redeem(token)
