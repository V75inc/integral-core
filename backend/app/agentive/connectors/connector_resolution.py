"""Connector row resolution for scoped invocation (per-user vs shared).

A workspace can hold several rows for one capability: each user's own
row plus at most one shared row. Tool specs (canonical keys) name the
*capability slug*, never a row — this module picks the row at invoke
time:

1. Personal row (``owner == principal``, slug match) wins.
2. Else the shared row (``connection_mode == "shared"``, slug match).
3. Else ``ResolutionError`` (``not_connected``) telling the user where
   to connect.

Slug match is ``catalog_slug`` (decrypted ``auth_state``) falling back
to ``subclass_slug`` — native rows carry the slug in both, MCP rows
only in ``auth_state`` (their ``subclass_slug`` is ``"mcp"``).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ResolutionError(Exception):
    """No usable connector row for (workspace, slug, principal)."""

    def __init__(self, message: str, error_code: str = "not_connected"):
        super().__init__(message, error_code)
        self.message = message
        self.error_code = error_code


def row_slug(connector: Any) -> str:
    """Capability slug for a row: catalog_slug, else subclass_slug."""
    try:
        from app.agentive.services.connector_registry_node import (
            decrypt_auth_state,
        )

        auth = decrypt_auth_state(getattr(connector, "auth_state", None) or {})
        slug = str(auth.get("catalog_slug") or "").strip()
        if slug:
            return slug
    except Exception:  # noqa: BLE001 — fall back to the subclass slug
        pass
    return str(getattr(connector, "subclass_slug", "") or "").strip()


def row_display_name(connector: Any, *, slug: str = "") -> str:
    """Human label: row label, else catalog display name, else slug."""
    label = str(getattr(connector, "label", "") or "").strip()
    if label:
        return label
    resolved = (slug or "").strip() or row_slug(connector)
    if resolved == "mcp":
        return "External tool server (MCP)"
    try:
        from app.connectors.catalog_loader import get_catalog_entry

        entry = get_catalog_entry(resolved)
        name = str(entry.get("display_name") or "").strip()
        if name:
            return name
    except (KeyError, ValueError):
        pass
    return (slug or row_slug(connector) or "connector").replace("_", " ")


def is_shared_row(connector: Any) -> bool:
    return (getattr(connector, "connection_mode", "") or "per_user") == "shared"


async def rows_for_workspace(workspace_id: str) -> List[Any]:
    """All connector rows mounted in a workspace (empty on lookup failure)."""
    try:
        from app.agentive.nodes import Connector

        found = await Connector.find({"workspace_id": workspace_id})
    except Exception:  # noqa: BLE001
        logger.warning("connector_resolution: connector lookup failed")
        return []
    return [c for c in (found or []) if c is not None]


async def resolve_connector_row(
    *,
    workspace_id: str,
    slug: str,
    principal_id: str,
) -> Any:
    """Pick the row ``principal_id`` invokes ``slug`` through in ``workspace``.

    Personal row wins; else the shared row; else ``ResolutionError`` with
    a message the agent can relay verbatim (names the connector + where
    to connect it).
    """
    slug = (slug or "").strip()
    if not slug:
        raise ResolutionError(
            "Connector capability is not specified",
            "misconfigured",
        )
    if not workspace_id:
        raise ResolutionError(
            "Connector tools require a bound workspace",
            "misconfigured",
        )
    connectors = await rows_for_workspace(workspace_id)
    personal = None
    shared = None
    for connector in connectors:
        if row_slug(connector) != slug:
            continue
        if principal_id and getattr(connector, "owner", "") == principal_id:
            personal = connector
            break
        if shared is None and is_shared_row(connector):
            shared = connector
    if personal is not None:
        return personal
    if shared is not None:
        return shared
    name: Optional[str] = None
    for connector in connectors:
        if row_slug(connector) == slug:
            name = row_display_name(connector)
            break
    if name is None:
        name = row_display_name(_EmptyRow(), slug=slug)
    raise ResolutionError(
        f"No {name} connection found — connect it in Settings → Connectors",
        "not_connected",
    )


class _EmptyRow:
    """Sentinel so display resolution never touches credential material."""

    label = ""
    subclass_slug = ""
    auth_state: Dict[str, Any] = {}
