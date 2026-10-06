"""Native request preparation carries facts without legacy intent routing."""

from unittest.mock import AsyncMock

import pytest

from app.api import ai_chat
from app.services.chat_providers import get_registry
from app.services.chat_providers.pydantic_ai_provider import PydanticAIProvider


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "utterance",
    [
        "I need a vehicle servicing register. Show me the setup; don't create it yet.",
        "Please do not save anything; keep this explanation in chat only.",
        "That sounds good. Go ahead.",
        "Add a service date field and show a dashboard of the next services.",
    ],
)
async def test_native_turn_does_not_invoke_legacy_judges_or_lexical_routing(
    authenticated_client, monkeypatch, utterance
):
    provider = PydanticAIProvider()
    monkeypatch.setattr(provider, "is_available", lambda: True)
    monkeypatch.setitem(get_registry()._providers, provider.id, provider)
    workspace = await authenticated_client.post(
        "/api/workspaces", json={"name": "Native preparation"}
    )
    assert workspace.status_code == 200, workspace.text
    workspace_id = workspace.json()["workspace"]["id"]
    headers = {"X-Integral-Scope": f"ws:{workspace_id}"}
    created = await authenticated_client.post(
        "/api/chat/threads",
        headers=headers,
        json={"provider_id": provider.id, "agent_id": "integral_core"},
    )
    assert created.status_code == 200, created.text
    thread_id = created.json()["id"]

    def legacy_routing_forbidden(*_args, **_kwargs):
        raise AssertionError("legacy intent routing entered the native path")

    for name in (
        "_requires_greenfield_proposal",
        "_is_explicit_no_workspace_write_request",
        "_is_existing_schema_field_request",
        "_is_dashboard_skill_request",
        "looks_like_bless",
        "_greenfield_proposal_error",
        "_approved_build_receipt_error",
    ):
        monkeypatch.setattr(ai_chat, name, legacy_routing_forbidden)
    monkeypatch.setattr(
        "app.services.query_plan.insights_plan_preamble", legacy_routing_forbidden
    )
    legacy_design_judge = AsyncMock(side_effect=legacy_routing_forbidden)
    monkeypatch.setattr(
        ai_chat.chat_store, "pending_design_context_for_utterance", legacy_design_judge
    )
    monkeypatch.setattr(
        ai_chat.chat_store, "stamp_design_approved", legacy_design_judge
    )
    seen = []

    async def stream_turn(ctx):
        seen.append(ctx)
        yield {"type": "text-delta", "delta": "Native response."}
        yield {"type": "message-finish", "timing": {"totalMs": 1}}

    monkeypatch.setattr(provider, "stream_turn", stream_turn)
    response = await authenticated_client.post(
        f"/api/chat/threads/{thread_id}/messages",
        headers=headers,
        json={"text": utterance},
    )
    assert response.status_code == 200, response.text
    assert len(seen) == 1
    ctx = seen[0]
    assert ctx.text == utterance
    assert ctx.workspace_id == workspace_id
    assert ctx.thread_id == thread_id
    assert ctx.extra_data["run_id"]
    assert "design_only" not in ctx.extra_data
    assert "no_workspace_writes" not in ctx.extra_data
    assert "use_skill" not in (ctx.system_context or "")
    assert "USER-CONFIRMED" not in (ctx.system_context or "")
    legacy_design_judge.assert_not_awaited()
