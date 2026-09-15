"""Parse ``X-Integral-Scope`` for workspace-scoped HTTP and agent tool calls."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def parse_scope_header(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """Parse ``X-Integral-Scope`` into a scope dict.

    Canonical: ``ws:<workspace_id>`` → {"kind": "workspace", "workspace_id": "<id>"}.
    Anything else → None.
    """
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.lower().startswith("ws:"):
        ws_id = text[3:].strip()
        if ws_id:
            return {"kind": "workspace", "workspace_id": ws_id}
    return None


def resolve_scope_from_request(request) -> Optional[Dict[str, Any]]:  # type: ignore[no-untyped-def]
    """Best-effort: read scope from the X-Integral-Scope header."""
    try:
        headers = getattr(request, "headers", None)
        if headers is None:
            return None
        return parse_scope_header(headers.get("x-integral-scope"))
    except Exception:
        logger.exception("resolve_scope_from_request failed")
        return None
