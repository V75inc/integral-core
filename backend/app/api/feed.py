"""Feed aggregation API endpoints for activity stream.

Provides a unified view of entries across all accessible tracks using
DB-level keyset pagination (``fetch_accessible_entries_page``).
"""

from typing import Any, Dict, List, Optional

from fastapi import Request
from jvspatial.api import endpoint

from app.api.entries import (
    create_entry,
    delete_entry,
    enrich_entry_page_for_response,
    update_entry,
)
from app.api.errors import MissingAuthenticationError
from app.api.utils import resolve_principal_id
from app.services.entry_listing import fetch_accessible_entries_page
from app.services.request_scope import resolve_workspace_id_from_request


@endpoint("/feed", methods=["GET"], auth=True, tags=["Feed"])
async def get_feed(
    request: Request,
    track_id: Optional[str] = None,
    app_id: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = 20,
    cross_workspace: bool = False,
    include_total: bool = True,
) -> Dict[str, Any]:
    """Get aggregated feed of entries across accessible tracks (cursor pagination).

    When ``cross_workspace=true``, ignore the ``X-Integral-Scope`` header and
    return entries from every workspace the caller can read. Used by the
    Mission Control bird's-eye view. Permission checks still apply via
    ``fetch_accessible_entries_page`` — the caller never sees entries they
    couldn't normally read.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    workspace_id: Optional[str]
    if cross_workspace:
        workspace_id = None
    else:
        workspace_id = await resolve_workspace_id_from_request(request, user_id)

    page_entries, base_response = await fetch_accessible_entries_page(
        user_id,
        track_id=track_id,
        app_id=app_id,
        workspace_id=workspace_id,
        cursor=cursor,
        limit=limit,
        include_total=include_total,
    )
    base_response["entries"] = await enrich_entry_page_for_response(page_entries)
    return base_response


@endpoint("/feed_entries", methods=["GET"], auth=True, tags=["Feed"])
async def get_feed_entries(
    request: Request,
    track_id: Optional[str] = None,
    app_id: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = 20,
    cross_workspace: bool = False,
    include_total: bool = True,
) -> Dict[str, Any]:
    """Get feed entries for a user (alias of /feed)."""
    return await get_feed(
        request,
        track_id,
        app_id,
        cursor,
        limit,
        cross_workspace,
        include_total,
    )


@endpoint("/feed_entries", methods=["POST"], auth=True, tags=["Feed"])
async def create_feed_entry(
    request: Request,
    track_id: str,
    title: str = "",
    type_id: str = "",
    body: Optional[str] = None,
    fields: Optional[Dict[str, Any]] = None,
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Create a new feed entry (wrapper for entry creation)."""
    return await create_entry(
        request,
        track_id,
        title=title,
        type_id=type_id,
        body=body,
        custom_fields=fields,
        tags=tags,
    )


@endpoint("/feed_entries/{entry_id}", methods=["PUT"], auth=True, tags=["Feed"])
async def update_feed_entry(
    request: Request,
    entry_id: str,
    body: Optional[str] = None,
    fields: Optional[Dict[str, Any]] = None,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    """Update a feed entry (wrapper for entry update)."""
    return await update_entry(
        request,
        entry_id,
        body=body,
        custom_fields=fields,
        status=status,
    )


@endpoint("/feed_entries/{entry_id}", methods=["DELETE"], auth=True, tags=["Feed"])
async def delete_feed_entry(
    request: Request,
    entry_id: str,
) -> Dict[str, Any]:
    """Delete a feed entry (wrapper for entry deletion).

    Args:
        entry_id: ID of the entry

    Returns:
        Deletion confirmation
    """
    return await delete_entry(request, entry_id)
