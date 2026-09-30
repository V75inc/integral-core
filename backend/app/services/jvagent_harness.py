"""Shared harness dispatch — bind BYOK ContextVar around jvagent turns."""

from __future__ import annotations

import contextlib
from typing import Any, AsyncIterator, Callable, Dict, Optional

from jvagent.action.model.context import bind_model_override

from app.services.model_credential_resolver import (
    ModelKeyRequiredError,
    resolve_agent_key_source,
    resolve_agent_model_override,
)


@contextlib.asynccontextmanager
async def harness_model_override(workspace_id: Optional[str]):
    """Resolve owner BYOK and bind jvagent per-turn override for one turn."""
    override = await resolve_agent_model_override(workspace_id)
    with bind_model_override(override):
        yield override


async def enforce_platform_ai_quota(workspace_id: Optional[str]) -> None:
    """Block platform-key turns when the workspace rolling credit cap is hit.

    BYOK turns are never gated here — the customer pays the provider directly.
    No registered limit resolver (open-source Core) means unlimited.
    """
    if not workspace_id:
        return
    source = await resolve_agent_key_source(workspace_id)
    if source != "platform":
        return
    from app.services.commercial_hooks import assert_ai_quota

    await assert_ai_quota(workspace_id)


async def stream_with_model_override(
    workspace_id: Optional[str],
    stream_factory: Callable[[], AsyncIterator[Dict[str, Any]]],
) -> AsyncIterator[Dict[str, Any]]:
    """Wrap an async stream factory with BYOK override + strict error surfacing."""
    try:
        await enforce_platform_ai_quota(workspace_id)
        async with harness_model_override(workspace_id):
            async for event in stream_factory():
                yield event
    except ModelKeyRequiredError as exc:
        yield {
            "type": "error",
            "code": "model_key_required",
            "message": str(exc) or "Configure your model API key in Settings",
        }
    except Exception as exc:
        # QuotaExceededError is a JVSpatialAPIException — surface as stream
        # error so SSE turns still complete cleanly for the client.
        from app.api.errors import QuotaExceededError

        if isinstance(exc, QuotaExceededError):
            yield {
                "type": "error",
                "code": getattr(exc, "error_code", None) or "ai_quota_exceeded",
                "message": str(exc) or "AI credit allowance exhausted",
            }
            return
        raise
