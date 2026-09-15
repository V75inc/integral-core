"""Reference Hello App tools — F0 external package."""

from __future__ import annotations

from typing import Any, Dict


async def echo(input: Dict[str, Any], ctx) -> Dict[str, Any]:
    """Return the caller's message — proves ToolContext dispatch for external packages."""
    message = str((input or {}).get("message") or "")
    return {"ok": True, "message": message, "workspace_id": getattr(ctx, "workspace_id", "")}
