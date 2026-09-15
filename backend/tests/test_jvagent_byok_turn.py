"""Integration tests — BYOK override bound for jvagent harness turns."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.jvagent_harness import stream_with_model_override
from app.services.model_credential_resolver import ModelKeyRequiredError


@pytest.mark.asyncio
async def test_stream_with_model_override_binds_jvagent_context():
    """Resolved BYOK dict is visible inside the stream via jvagent ContextVar."""
    from jvagent.action.model.context import get_model_override

    override = {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "api_key": "sk-harness-test",
    }

    async def _fake_resolve(_workspace_id):
        return override

    seen = {}

    async def _stream_factory():
        seen["override"] = get_model_override()
        yield {"type": "done"}

    with patch(
        "app.services.jvagent_harness.resolve_agent_model_override",
        new=AsyncMock(side_effect=_fake_resolve),
    ):
        events = []
        async for event in stream_with_model_override("ws-test", _stream_factory):
            events.append(event)

    assert seen["override"] == override
    assert events == [{"type": "done"}]


@pytest.mark.asyncio
async def test_stream_surfaces_model_key_required_error():
    """Surface a missing-key error as a model_key_required event, not a raise."""

    async def _stream_factory():
        yield {"type": "should-not-run"}
        if False:
            yield {}

    with patch(
        "app.services.jvagent_harness.resolve_agent_model_override",
        new=AsyncMock(side_effect=ModelKeyRequiredError("model_key_required")),
    ):
        events = []
        async for event in stream_with_model_override("ws-test", _stream_factory):
            events.append(event)

    assert events == [
        {
            "type": "error",
            "code": "model_key_required",
            "message": "model_key_required",
        }
    ]
