"""Platform authority, concurrent edits, and native turn enforcement."""

import asyncio

import pytest

from app.agentive.harness.pydantic_ai_compat import UsageLimitExceeded
from app.api.errors import ResourceConflictError
from app.config import settings
from app.schemas.deployment_chat_limits import ChatLimitsWrite
from app.services.chat_latency import current_latency, turn_usage_limits
from app.services.chat_providers.pydantic_ai_provider import PydanticAIProvider
from app.services.deployment_chat_limits import read_limits, save_limits

pytestmark = pytest.mark.asyncio
WRITE = "/api/admin/chat-limits"
READ = "/api/users/me/ai-configuration"


async def test_authority_save_readback_conflict_and_reset(
    authenticated_admin_client, authenticated_client, monkeypatch
):
    monkeypatch.setattr(settings, "INTEGRAL_NATIVE_TURN_REQUEST_LIMIT", 12)
    admin = authenticated_admin_client
    user = authenticated_client
    default = (await admin.get(READ)).json()["deployment_limit_settings"]
    assert default["overrides"] == {}
    version = default["revision"]
    changed = {"revision": version, "overrides": {"request_limit": 4}}
    assert (await user.post(WRITE, json=changed)).status_code == 403
    assert (
        await admin.post(WRITE, json={**changed, "overrides": {"request_limit": 13}})
    ).status_code == 400
    assert (
        await admin.post(WRITE, json={**changed, "overrides": {"unknown": 1}})
    ).status_code == 400
    saved = await admin.post(WRITE, json=changed)
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == version + 1
    assert saved.json()["effective"]["request_limit"] == 4
    view = (await user.get(READ)).json()
    assert "deployment_limit_settings" not in view
    assert view["harness_limits"]["request_limit"] == 4
    assert (await admin.post(WRITE, json=changed)).status_code == 409
    monkeypatch.setattr(settings, "INTEGRAL_NATIVE_TURN_REQUEST_LIMIT", 3)
    assert (await read_limits())["effective"]["request_limit"] == 3
    restored = await admin.post(WRITE, json={"revision": version + 1, "overrides": {}})
    assert restored.status_code == 200
    assert restored.json()["effective"] == restored.json()["host"]
    assert (await admin.get(READ)).json()[
        "deployment_limit_settings"
    ] == restored.json()


async def test_two_admins_cannot_overwrite_the_same_revision():
    version = (await read_limits())["revision"]

    async def write(n):
        return await save_limits(
            "qa", ChatLimitsWrite(revision=version, overrides={"tool_call_limit": n})
        )

    results = await asyncio.gather(write(2), write(3), return_exceptions=True)
    assert sum(isinstance(r, ResourceConflictError) for r in results) == 1
    assert (await read_limits())["revision"] == version + 1


async def test_next_native_turn_uses_saved_limits_and_profile_can_tighten(monkeypatch):
    original = await read_limits()
    overrides = dict(
        request_limit=2,
        tool_call_limit=3,
        token_limit=555,
        run_seconds=60,
        request_timeout=15,
        idle_timeout=8,
    )
    await save_limits(
        "qa", ChatLimitsWrite(revision=original["revision"], overrides=overrides)
    )
    provider = PydanticAIProvider()

    async def probe(ctx):
        timing = current_latency.get()
        assert 0 < timing.remaining_deadline_seconds() <= 60
        assert timing.budget_settings["request_timeout"] == 15
        assert timing.budget_settings["idle_timeout"] == 8
        limits = turn_usage_limits()
        assert (
            limits.request_limit,
            limits.tool_calls_limit,
            limits.total_tokens_limit,
        ) == (2, 3, 555)
        timing.freeze_budget({"model_request_limit": 1, "turn_token_limit": 999})
        assert turn_usage_limits().total_tokens_limit == 555
        timing.reserve_dispatch()
        with pytest.raises(UsageLimitExceeded):
            timing.reserve_dispatch()
        yield {"type": "message-finish"}

    monkeypatch.setattr(provider, "_stream_turn", probe)
    assert [e["type"] async for e in provider.stream_turn(None)] == ["message-finish"]
