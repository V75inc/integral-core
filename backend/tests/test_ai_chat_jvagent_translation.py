"""jvchat SSE → normalized envelope translator coverage.

Drives the translator with a fake httpx ASGI transport that streams a
canned jvchat event sequence, then asserts the normalized output matches the
SPEC § 6.3 envelope.
"""

from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator, Dict, List

import httpx
import pytest

from app.providers.jvagent_streaming import stream_jvagent_turn


def _sse(payload: Dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode("utf-8")


async def _collect(stream: AsyncIterator[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [ev async for ev in stream]


def _fake_transport(chunks: List[bytes], status_code: int = 200) -> httpx.AsyncClient:
    """An httpx client that returns the given SSE chunks for any POST."""

    async def app(scope, receive, send):  # type: ignore[no-untyped-def]
        assert scope["type"] == "http"
        # Drain request
        more = True
        while more:
            msg = await receive()
            more = msg.get("more_body", False)
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        for i, chunk in enumerate(chunks):
            await send(
                {
                    "type": "http.response.body",
                    "body": chunk,
                    "more_body": i < len(chunks) - 1,
                }
            )

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app))


@pytest.mark.asyncio
async def test_translator_emits_text_reasoning_tool_step_finish() -> None:
    chunks = [
        _sse(
            {
                "type": "start",
                "interaction_id": "i1",
                "session_id": "s1",
                "user_id": "u1",
            }
        ),
        _sse(
            {
                "type": "message",
                "message": {
                    "category": "thought",
                    "thought_type": "reasoning",
                    "content": "Thinking…",
                    "segment_id": "seg-a",
                },
            }
        ),
        _sse(
            {
                "type": "message",
                "message": {
                    "category": "thought",
                    "thought_type": "tool_call",
                    "content": '{"q":"hi"}',
                    "segment_id": "tc-1",
                    "tool_name": "search",
                },
            }
        ),
        _sse(
            {
                "type": "message",
                "message": {
                    "category": "thought",
                    "thought_type": "tool_result",
                    "content": '{"ok":true}',
                    "segment_id": "tc-1",
                    "tool_name": "search",
                },
            }
        ),
        _sse(
            {
                "type": "message",
                "message": {"category": "user", "content": "Hello "},
            }
        ),
        _sse(
            {
                "type": "message",
                "message": {"category": "user", "content": "world!"},
            }
        ),
        _sse(
            {
                "type": "final",
                "interaction": {
                    "response": "Hello world!",
                    "observability_metrics": [
                        {
                            "model_call": {
                                "model": "test-model",
                                "usage": {
                                    "input_tokens": 10,
                                    "output_tokens": 4,
                                },
                                "finish_reason": "stop",
                            }
                        }
                    ],
                },
            }
        ),
    ]

    async with _fake_transport(chunks) as client:
        events = await _collect(
            stream_jvagent_turn(
                base_url="http://fake",
                agent_id="agentX",
                user_id="user@example.com",
                text="hi",
                session_id=None,
                channel="integral-ai-chat",
                start_time=time.monotonic(),
                client=client,
            )
        )

    types = [e["type"] for e in events]
    # _meta (from start), reasoning, tool-call running, tool-call complete,
    # two text deltas, then on the `final` chunk: final-content (authoritative
    # answer + full chunk for the debug view), step (usage), finish (timing).
    assert types == [
        "_meta",
        "reasoning-delta",
        "tool-call",
        "tool-call",
        "text-delta",
        "text-delta",
        "final-content",
        "step",
        "message-finish",
    ]

    meta = events[0]
    assert meta["provider_session_id"] == "s1"
    assert meta["provider_interaction_id"] == "i1"

    reasoning = events[1]
    assert reasoning["delta"] == "Thinking…"
    assert reasoning["segmentId"] == "seg-a"

    tool_running, tool_complete = events[2], events[3]
    assert tool_running["status"] == "running"
    assert tool_running["toolCallId"] == "tc-1"
    assert tool_running["name"] == "search"
    assert tool_complete["status"] == "complete"
    assert tool_complete["result"] == '{"ok":true}'

    assert events[4]["delta"] == "Hello "
    assert events[5]["delta"] == "world!"

    # final-content carries the authoritative answer text + the full final
    # chunk (the debug view's two panels).
    final_content = events[6]
    assert final_content["content"] == "Hello world!"
    assert final_content["payload"]["type"] == "final"
    assert final_content["payload"]["interaction"]["response"] == "Hello world!"

    step = events[7]
    assert step["usage"] == {"inputTokens": 10, "outputTokens": 4}
    assert step["modelId"] == "test-model"
    assert step["finishReason"] == "stop"

    finish = events[8]
    assert "totalMs" in finish["timing"]
    assert finish["timing"].get("firstTokenMs") is not None


@pytest.mark.asyncio
async def test_translator_emits_message_boundary_between_distinct_user_messages() -> (
    None
):
    """Two distinct adhoc user messages (e.g. intro greeting then the answer)
    each carry their own ``id``. The translator must emit a single
    ``message-boundary`` between them so the UI renders SEPARATE bubbles, and
    must NOT emit one between stream-chunks that share one id."""
    chunks = [
        _sse({"type": "start", "interaction_id": "i1", "session_id": "s1"}),
        # Intro greeting — its own adhoc message id.
        _sse(
            {
                "type": "message",
                "message": {
                    "id": "m-intro",
                    "category": "user",
                    "message_type": "adhoc",
                    "content": "Hello! I'm Integral's assistant.",
                },
            }
        ),
        # The actual answer — a different message id, delivered as two chunks
        # that SHARE that id (no boundary between the two chunks).
        _sse(
            {
                "type": "message",
                "message": {
                    "id": "m-answer",
                    "category": "user",
                    "message_type": "stream_chunk",
                    "content": "Your highest ",
                },
            }
        ),
        _sse(
            {
                "type": "message",
                "message": {
                    "id": "m-answer",
                    "category": "user",
                    "message_type": "stream_chunk",
                    "content": "grossing project is X.",
                },
            }
        ),
    ]

    async with _fake_transport(chunks) as client:
        events = await _collect(
            stream_jvagent_turn(
                base_url="http://fake",
                agent_id="agentX",
                user_id="user@example.com",
                text="hi",
                session_id=None,
                channel="integral-ai-chat",
                start_time=time.monotonic(),
                client=client,
            )
        )

    types = [e["type"] for e in events]
    # _meta, intro text, ONE boundary, then the two answer chunks (no boundary
    # between same-id chunks).
    assert types == [
        "_meta",
        "text-delta",
        "message-boundary",
        "text-delta",
        "text-delta",
    ]
    assert events[1]["delta"] == "Hello! I'm Integral's assistant."
    assert events[3]["delta"] == "Your highest "
    assert events[4]["delta"] == "grossing project is X."


@pytest.mark.asyncio
async def test_translator_ignores_bus_final_as_user_text() -> None:
    """message_type=final is end-of-stream, not a new assistant identity.

    finalize_interaction used to mint a fresh Object id for that empty frame.
    Treating it as user text emitted a message-boundary and a second bubble.
    """
    chunks = [
        _sse({"type": "start", "interaction_id": "i1", "session_id": "s1"}),
        _sse(
            {
                "type": "message",
                "message": {
                    "id": "m-answer",
                    "category": "user",
                    "message_type": "stream_chunk",
                    "content": "Ships Tuesday.",
                },
            }
        ),
        _sse(
            {
                "type": "message",
                "message": {
                    "id": "m-finalize-new-uuid",
                    "category": "user",
                    "message_type": "final",
                    "content": "",
                },
            }
        ),
        _sse(
            {
                "type": "final",
                "interaction": {"id": "i1", "response": "Ships Tuesday."},
            }
        ),
    ]

    async with _fake_transport(chunks) as client:
        events = await _collect(
            stream_jvagent_turn(
                base_url="http://fake",
                agent_id="agentX",
                user_id="user@example.com",
                text="when",
                session_id=None,
                channel="integral-ai-chat",
                start_time=time.monotonic(),
                client=client,
            )
        )

    types = [e["type"] for e in events]
    assert "message-boundary" not in types
    assert types.count("text-delta") == 1
    assert events[1]["delta"] == "Ships Tuesday."


@pytest.mark.asyncio
async def test_translator_collapses_duplicate_hello_same_interaction() -> None:
    """Fresh-session Hello used to land twice: two user ids, same prose.

    The bus latch is the source fix; this is defense in depth so a second
    full reply on the same interaction never splits another bubble.
    """
    chunks = [
        _sse({"type": "start", "interaction_id": "i1", "session_id": "s1"}),
        _sse(
            {
                "type": "message",
                "message": {
                    "id": "m-hello-1",
                    "category": "user",
                    "message_type": "stream_chunk",
                    "interaction_id": "i1",
                    "content": "Hello! I'm Integral's assistant.",
                },
            }
        ),
        _sse(
            {
                "type": "message",
                "message": {
                    "id": "m-hello-2",
                    "category": "user",
                    "message_type": "adhoc",
                    "interaction_id": "i1",
                    "content": "Hello! I'm Integral's assistant.",
                },
            }
        ),
        _sse(
            {
                "type": "final",
                "interaction": {
                    "id": "i1",
                    "response": "Hello! I'm Integral's assistant.",
                },
            }
        ),
    ]

    async with _fake_transport(chunks) as client:
        events = await _collect(
            stream_jvagent_turn(
                base_url="http://fake",
                agent_id="agentX",
                user_id="user@example.com",
                text="Hello",
                session_id=None,
                channel="integral-ai-chat",
                start_time=time.monotonic(),
                client=client,
            )
        )

    types = [e["type"] for e in events]
    assert "message-boundary" not in types
    deltas = [e["delta"] for e in events if e["type"] == "text-delta"]
    assert deltas == ["Hello! I'm Integral's assistant."]


@pytest.mark.asyncio
async def test_translator_claim_provenance_tags_page_context_vs_query() -> None:
    """Debug payload must distinguish page-context from a QuerySpec tool."""
    chunks = [
        _sse({"type": "start", "interaction_id": "i1", "session_id": "s1"}),
        _sse(
            {
                "type": "message",
                "message": {
                    "id": "tc-page",
                    "category": "thought",
                    "thought_type": "tool_result",
                    "tool_name": "integral_get_page_context",
                    "content": "{}",
                    "metadata": {
                        "tool_name": "integral_get_page_context",
                        "tool_call_id": "tc-page",
                        "tool_args": {"include": "all"},
                        "tool_result": {"source": "page_context_snapshot"},
                    },
                },
            }
        ),
        _sse(
            {
                "type": "message",
                "message": {
                    "id": "tc-query",
                    "category": "thought",
                    "thought_type": "tool_result",
                    "tool_name": "integral_list_apps",
                    "content": "{}",
                    "metadata": {
                        "tool_name": "integral_list_apps",
                        "tool_call_id": "tc-query",
                        "tool_args": {"limit": 50},
                        "tool_result": {"items": []},
                    },
                },
            }
        ),
        _sse(
            {
                "type": "final",
                "interaction": {"id": "i1", "response": "0 apps."},
            }
        ),
    ]

    async with _fake_transport(chunks) as client:
        events = await _collect(
            stream_jvagent_turn(
                base_url="http://fake",
                agent_id="agentX",
                user_id="user@example.com",
                text="how many apps",
                session_id=None,
                channel="integral-ai-chat",
                start_time=time.monotonic(),
                client=client,
            )
        )

    finals = [e for e in events if e["type"] == "final-content"]
    assert len(finals) == 1
    prov = finals[0]["payload"]["claim_provenance"]
    sources = {t["name"]: t["source"] for t in prov["tools"]}
    assert sources["integral_get_page_context"] == "page_context"
    assert sources["integral_list_apps"] == "query"
    assert prov["substrate_query_executed"] is True
    assert prov["page_context_tool_executed"] is True
    assert {"limit": 50} in prov["query_plan"]


@pytest.mark.asyncio
async def test_translator_surfaces_http_error() -> None:
    async with _fake_transport([b""], status_code=502) as client:
        events = await _collect(
            stream_jvagent_turn(
                base_url="http://fake",
                agent_id="agentX",
                user_id="user@example.com",
                text="hi",
                session_id=None,
                channel="integral-ai-chat",
                client=client,
            )
        )
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert events[0]["code"] == "jvagent_http_502"


@pytest.mark.asyncio
async def test_translator_surfaces_jvchat_error_event() -> None:
    chunks = [_sse({"type": "error", "message": "model down"})]
    async with _fake_transport(chunks) as client:
        events = await _collect(
            stream_jvagent_turn(
                base_url="http://fake",
                agent_id="agentX",
                user_id="user@example.com",
                text="hi",
                session_id=None,
                channel="integral-ai-chat",
                client=client,
            )
        )
    assert events == [
        {"type": "error", "code": "jvagent_error", "message": "model down"}
    ]


def test_extract_usage_step_reads_jvagent_event_type_shape() -> None:
    """Live jvagent metrics use event_type + data, not a nested model_call key."""
    from app.providers.jvagent_streaming import _extract_usage_step

    step = _extract_usage_step(
        {
            "event_type": "model_call",
            "data": {
                "model": "gpt-4o-mini",
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
                "finish_reason": "stop",
            },
        }
    )
    assert step == {
        "type": "step",
        "usage": {"inputTokens": 11, "outputTokens": 7},
        "modelId": "gpt-4o-mini",
        "finishReason": "stop",
    }

    assert (
        _extract_usage_step({"event_type": "tool_call", "data": {"usage": {}}}) is None
    )
