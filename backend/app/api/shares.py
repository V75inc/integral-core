"""Phase 4 — share-link API endpoints.

Endpoints:

* ``POST   /{apps|tracks|entries}/{id}/shares``   — owner mints a link.
* ``GET    /{apps|tracks|entries}/{id}/shares``   — list active links
                                                       for that resource.
* ``DELETE /shares/{share_link_id}``                — owner revokes a link.
* ``POST   /shares/redeem``                         — signed-in caller
                                                       redeems a token.

POST handlers parse JSON via ``request.json()`` so jvspatial's synthetic body
wrapper does not swallow the ``Request`` parameter (see ``api/retrieve.py``).
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import BadRequestError, MissingAuthenticationError
from app.api.utils import resolve_principal_id
from app.schemas.shares import MintShareLinkRequest, RedeemShareLinkRequest
from app.services.share_links import (
    list_active_links,
    mint_share_link,
    redeem_share_link,
    revoke_share_link,
)


def _require_user(request: Request) -> str:
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    return user_id


async def _parse_mint_body(request: Request) -> Tuple[str, Optional[str]]:
    try:
        raw = await request.json()
    except Exception as exc:
        raise BadRequestError(message=f"Invalid JSON body: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise BadRequestError(message="Request body must be a JSON object.")
    body = MintShareLinkRequest.model_validate(raw)
    return body.role, body.expires_at


async def _parse_redeem_body(request: Request) -> str:
    try:
        raw = await request.json()
    except Exception as exc:
        raise BadRequestError(message=f"Invalid JSON body: {exc}") from exc
    if not isinstance(raw, dict):
        raise BadRequestError(message="Request body must be a JSON object.")
    body = RedeemShareLinkRequest.model_validate(raw)
    return body.token


# ---------------------------------------------------------------------------
# Mint (per resource type)
# ---------------------------------------------------------------------------


@endpoint(
    "/apps/{app_id}/shares",
    methods=["POST"],
    auth=True,
    tags=["Shares"],
)
async def mint_space_link(request: Request, app_id: str) -> Dict[str, Any]:
    """Owner mints a share link for an App."""
    user_id = _require_user(request)
    role, expires_at = await _parse_mint_body(request)
    return await mint_share_link(user_id, "app", app_id, role, expires_at)


@endpoint(
    "/tracks/{track_id}/shares",
    methods=["POST"],
    auth=True,
    tags=["Shares"],
)
async def mint_track_link(request: Request, track_id: str) -> Dict[str, Any]:
    """Owner mints a share link for a Track."""
    user_id = _require_user(request)
    role, expires_at = await _parse_mint_body(request)
    return await mint_share_link(user_id, "track", track_id, role, expires_at)


@endpoint(
    "/entries/{entry_id}/shares",
    methods=["POST"],
    auth=True,
    tags=["Shares"],
)
async def mint_entry_link(request: Request, entry_id: str) -> Dict[str, Any]:
    """Owner mints a share link for an Entry."""
    user_id = _require_user(request)
    role, expires_at = await _parse_mint_body(request)
    return await mint_share_link(user_id, "entry", entry_id, role, expires_at)


# ---------------------------------------------------------------------------
# List active links (per resource)
# ---------------------------------------------------------------------------


@endpoint(
    "/apps/{app_id}/shares",
    methods=["GET"],
    auth=True,
    tags=["Shares"],
)
async def list_space_links(request: Request, app_id: str) -> Dict[str, Any]:
    """List active share-links minted for ``app_id``."""
    user_id = _require_user(request)
    return {"links": await list_active_links(user_id, "app", app_id)}


@endpoint(
    "/tracks/{track_id}/shares",
    methods=["GET"],
    auth=True,
    tags=["Shares"],
)
async def list_track_links(request: Request, track_id: str) -> Dict[str, Any]:
    """List active share-links minted for ``track_id``."""
    user_id = _require_user(request)
    return {"links": await list_active_links(user_id, "track", track_id)}


@endpoint(
    "/entries/{entry_id}/shares",
    methods=["GET"],
    auth=True,
    tags=["Shares"],
)
async def list_entry_links(request: Request, entry_id: str) -> Dict[str, Any]:
    """List active share-links minted for ``entry_id``."""
    user_id = _require_user(request)
    return {"links": await list_active_links(user_id, "entry", entry_id)}


# ---------------------------------------------------------------------------
# Revoke + redeem (resource-agnostic)
# ---------------------------------------------------------------------------


@endpoint(
    "/shares/{share_link_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Shares"],
)
async def revoke_link(request: Request, share_link_id: str) -> Dict[str, Any]:
    """Owner-only revoke. Subsequent redemption attempts return 4xx."""
    user_id = _require_user(request)
    return await revoke_share_link(user_id, share_link_id)


@endpoint(
    "/shares/redeem",
    methods=["POST"],
    auth=True,
    tags=["Shares"],
)
async def redeem_link(request: Request) -> Dict[str, Any]:
    """Signed-in token redemption. Materializes COLLABORATES_ON + guest IS_MEMBER_OF."""
    user_id = _require_user(request)
    token = await _parse_redeem_body(request)
    return await redeem_share_link(user_id, token)
