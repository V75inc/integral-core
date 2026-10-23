"""Content-free timing, bounded summaries and immutable discovery reuse."""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.services.chat_latency import TurnLatency, current_latency, mark_latency
from app.services.chat_tool_summary import normalize_tool_summary, tool_summary
from app.services.chat_turn_events import normalize_public_event

pytestmark = pytest.mark.smoke


def test_public_summary_retains_counts_not_records_and_rejects_raw_fields():
    summary = tool_summary(
        "integral_query_entries", {"entries": [{"secret": "private"}]}, failed=False
    )
    public = normalize_public_event(
        {
            "type": "tool-call",
            "name": "integral_query_entries",
            "toolCallId": "call",
            "status": "complete",
            "args": {"key": "secret"},
            "result": {"secret": True},
            "summary": summary,
        }
    )
    assert public["summary"] == {"outcome": "returned", "returned_count": 1}
    assert "secret" not in str(public)
    with pytest.raises(ValidationError):
        normalize_tool_summary({"outcome": "returned", "secret": "private"})
    assert tool_summary("mcp__external", {"items": [1, 2]}, failed=False) == {
        "outcome": "completed"
    }


@pytest.mark.asyncio
async def test_latency_isolated_between_concurrent_turns_and_first_mark_stable():
    async def run(name):
        timing = TurnLatency()
        token = current_latency.set(timing)
        try:
            await asyncio.sleep(0)
            mark_latency(name)
            first = dict(timing.markers)
            mark_latency(name)
            assert first == timing.markers
            return timing.markers
        finally:
            current_latency.reset(token)

    a, b = await asyncio.gather(run("firstToolStartedMs"), run("firstTokenMs"))
    assert set(a) == {"firstToolStartedMs"} and set(b) == {"firstTokenMs"}
    assert current_latency.get() is None


@pytest.mark.asyncio
async def test_discovery_reuses_document_vectors_but_not_authorized_membership(
    monkeypatch,
):
    from app.agentive.harness import capability_search as module

    module._DEFINITION_VECTORS.clear()
    calls = []

    async def embed(text):
        calls.append(text)
        return [1.0, 0.0]

    monkeypatch.setattr(module, "_embed_text_for_capability_search", embed)
    definitions = [
        SimpleNamespace(name=n, description=n, parameters_json_schema={})
        for n in ["allowed_tool", "private_tool"]
    ]
    await module.pydantic_tool_search_strategy(None, ["query"], definitions)
    count = len(calls)
    result = await module.pydantic_tool_search_strategy(
        None, ["query"], definitions[:1]
    )
    assert len(calls) == count + 1  # Only this query is embedded again.
    assert "private_tool" not in result
    module._DEFINITION_VECTORS.clear()


@pytest.mark.asyncio
async def test_bounded_chat_workers_can_start_independent_turns(monkeypatch):
    from app.agentive.services import work_worker

    started = set()
    all_started = asyncio.Event()
    release = asyncio.Event()
    stop = asyncio.Event()

    async def process(*, worker_id, **kwargs):
        started.add(worker_id)
        if len(started) == 2:
            all_started.set()
        await release.wait()
        stop.set()
        return object()

    monkeypatch.setattr(work_worker, "process_one_due_item", process)
    tasks = [
        asyncio.create_task(
            work_worker.worker_loop(
                worker_id=f"chat-{i}", kinds=frozenset({"chat_turn"}), stop_event=stop
            )
        )
        for i in range(2)
    ]
    try:
        await asyncio.wait_for(all_started.wait(), 2)
        assert not release.is_set()  # Neither turn needed the other to finish.
    finally:
        release.set()
        await asyncio.gather(*tasks)


def test_profile_budgets_include_adaptive_pass_and_never_extend_host_limits():
    from app.config import settings
    from app.services.chat_latency import turn_usage_limits

    timing = TurnLatency(
        model_settings={
            "model_request_limit": 3,
            "tool_call_limit": 5,
            "turn_token_limit": 5000,
        }
    )
    token = current_latency.set(timing)
    try:
        limits = turn_usage_limits(adaptive_requests=1, adaptive_tokens=800)
        assert limits.request_limit == 2
        assert limits.tool_calls_limit == 5
        assert limits.total_tokens_limit == 4200
        assert turn_usage_limits(light=True).request_limit == 1
        timing.model_settings = {
            "model_request_limit": 1000,
            "turn_token_limit": 9_000_000,
            "tool_call_limit": 1000,
        }
        limits = turn_usage_limits()
        assert limits.request_limit == settings.INTEGRAL_NATIVE_TURN_REQUEST_LIMIT
        assert limits.total_tokens_limit == settings.INTEGRAL_NATIVE_TURN_TOKEN_LIMIT
        assert limits.tool_calls_limit == 32
    finally:
        current_latency.reset(token)


@pytest.mark.asyncio
async def test_turn_deadline_cancels_provider_and_resets_context(monkeypatch):
    from app.config import settings
    from app.services.chat_providers.pydantic_ai_provider import PydanticAIProvider

    closed = asyncio.Event()

    async def slow(_ctx):
        try:
            await asyncio.Event().wait()
            yield {"type": "message-finish"}
        finally:
            closed.set()

    provider = PydanticAIProvider()
    monkeypatch.setattr(provider, "_stream_turn", slow)
    monkeypatch.setattr(settings, "INTEGRAL_HARNESS_CHAT_TURN_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(TimeoutError):
        _ = [event async for event in provider.stream_turn(SimpleNamespace())]
    assert closed.is_set()
    assert current_latency.get() is None
