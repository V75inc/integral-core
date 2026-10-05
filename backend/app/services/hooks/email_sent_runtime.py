"""Dispatch bundle hooks after outbound email (email.sent)."""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


async def run_email_sent_hooks(
    *,
    workspace_id: str,
    actor_id: str,
    payload: Dict[str, Any],
) -> None:
    """Best-effort ``email.sent`` hook dispatch. Never raises."""
    from app.services.hooks.registry import ToolContext, get_workspace_tools
    from app.services.hooks.resolver import find_matching_bindings
    from app.services.hooks.tool_dispatch import run_tool

    ws = str(workspace_id or "").strip()
    if not ws:
        return

    actor = str(actor_id or "").strip()
    if not actor:
        logger.warning(
            "email_sent_runtime: skipping hooks — no actor_id (ws=%s to=%s)",
            ws,
            payload.get("to"),
        )
        return

    tools = get_workspace_tools(ws)
    ctx = ToolContext(
        user_id=actor,
        workspace_id=ws,
        scope="email:sent",
    )

    for binding in find_matching_bindings(ws, "email.sent", payload):
        if str(binding.get("mode") or "") != "tool":
            continue
        tool_key = str(binding.get("tool") or "")
        spec = tools.get(tool_key)
        if not spec:
            logger.warning(
                "email_sent_runtime: binding %r missing tool %r (ws=%s)",
                binding.get("key"),
                tool_key,
                ws,
            )
            continue
        try:
            await run_tool(spec, payload, ctx)
        except Exception:
            logger.exception(
                "email_sent_runtime: hook %r failed (ws=%s to=%s)",
                binding.get("key"),
                ws,
                payload.get("to"),
            )
