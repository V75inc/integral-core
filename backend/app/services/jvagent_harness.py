"""Shared harness dispatch — bind BYOK ContextVar around jvagent turns."""

from __future__ import annotations

import contextlib
from typing import Any, AsyncIterator, Callable, Dict, Optional

from jvagent.action.model.context import bind_model_override

from app.services.model_credential_resolver import (
    ModelKeyRequiredError,
    resolve_agent_model_override,
)


@contextlib.asynccontextmanager
async def harness_model_override(workspace_id: Optional[str]):
    """Resolve owner BYOK and bind jvagent per-turn override for one turn."""
    override = await resolve_agent_model_override(workspace_id)
    with bind_model_override(override):
        yield override


async def stream_with_model_override(
    workspace_id: Optional[str],
    stream_factory: Callable[[], AsyncIterator[Dict[str, Any]]],
) -> AsyncIterator[Dict[str, Any]]:
    """Wrap an async stream factory with BYOK override + strict error surfacing."""
    try:
        async with harness_model_override(workspace_id):
            async for event in stream_factory():
                yield event
    except ModelKeyRequiredError as exc:
        yield {
            "type": "error",
            "code": "model_key_required",
            "message": str(exc) or "Configure your model API key in Settings",
        }
