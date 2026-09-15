"""A tool failure the agent recovered from must not read as a failed turn.

Reported in testing (2026-08-05): asked to "find all bugs related to Safari",
the resident tried ``integral_query``, fell back, and answered correctly — "No
bugs related to Safari were found. The Bugs track has only one entry … Want me
to search across all tracks in the workspace?" — and the user still saw a red
"Action 'integral_query' could not be completed." underneath it.

A turn that succeeded reading as a turn that failed is worse than a missing
banner: it trains people to ignore the banner exactly when it is telling the
truth. So the turn-level error waits for the verdict — dropped when an answer
lands, emitted when none does.

The failure is never hidden. The tool-call event keeps ``status="error"``, so
it stays visible in the tech-detail disclosure either way; that is asserted
here so a future change cannot "fix" the banner by silencing the tool too.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Dict, List

import httpx
import pytest

from app.providers.jvagent_streaming import stream_jvagent_turn

pytestmark = pytest.mark.smoke


def _sse(payload: Dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode("utf-8")


async def _collect(stream: AsyncIterator[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [ev async for ev in stream]


def _fake_transport(chunks: List[bytes]) -> httpx.AsyncClient:
    async def app(scope, receive, send):  # type: ignore[no-untyped-def]
        more = True
        while more:
            msg = await receive()
            more = msg.get("more_body", False)
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        for chunk in chunks:
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app))


def _failed_tool(tool_name: str = "integral_query") -> bytes:
    return _sse(
        {
            "type": "message",
            "message": {
                "category": "thought",
                "thought_type": "tool_result",
                "content": '{"error":"query_failed"}',
                "segment_id": "tc-1",
                "tool_name": tool_name,
                "metadata": {"is_error": True, "tool_name": tool_name},
            },
        }
    )


def _final(response: Any) -> bytes:
    return _sse({"type": "final", "interaction": {"response": response}})


async def _run(chunks: List[bytes]) -> List[Dict[str, Any]]:
    async with _fake_transport(chunks) as client:
        return await _collect(
            stream_jvagent_turn(
                base_url="http://x",
                agent_id="a",
                user_id="u",
                text="find all bugs related to Safari",
                session_id=None,
                channel="web",
                client=client,
            )
        )


@pytest.mark.asyncio
async def test_recovered_failure_does_not_emit_a_turn_level_error():
    """The reported bug: correct answer, red banner anyway."""
    events = await _run(
        [
            _failed_tool(),
            _final("No bugs related to Safari were found."),
        ]
    )

    errors = [e for e in events if e.get("type") == "error"]
    assert errors == [], f"recovered turn surfaced a failure banner: {errors}"

    finals = [e for e in events if e.get("type") == "final-content"]
    assert finals and finals[0]["content"].startswith("No bugs related to Safari")


@pytest.mark.asyncio
async def test_the_tool_call_is_still_marked_failed():
    """Suppressing the banner must not also hide the tool failure."""
    events = await _run([_failed_tool(), _final("Answered anyway.")])

    tool_events = [e for e in events if e.get("type") == "tool-call"]
    assert tool_events, "no tool-call event emitted"
    assert any(e.get("status") == "error" for e in tool_events), (
        "the failed tool call lost its error status — the failure would be "
        "invisible in the tech-detail disclosure too"
    )


@pytest.mark.asyncio
async def test_unrecovered_failure_still_surfaces():
    """No answer means the failure IS the outcome and must be shown."""
    events = await _run([_failed_tool(), _final(None)])

    errors = [e for e in events if e.get("type") == "error"]
    assert len(errors) == 1, f"expected the banner, got {errors}"
    assert errors[0]["code"] == "tool_failed"
    assert "integral_query" in errors[0]["message"]


@pytest.mark.asyncio
async def test_empty_string_answer_counts_as_no_answer():
    """An empty response is not a recovery — the user has nothing to read."""
    events = await _run([_failed_tool(), _final("")])

    assert [
        e for e in events if e.get("type") == "error"
    ], "an empty answer suppressed the banner, leaving the turn silent"


@pytest.mark.asyncio
async def test_error_precedes_the_final_content_event():
    """Ordering matters: the FE renders final-content as the settled answer."""
    events = await _run([_failed_tool(), _final(None)])
    kinds = [e.get("type") for e in events]
    assert kinds.index("error") < kinds.index("final-content")


@pytest.mark.asyncio
async def test_every_failed_tool_surfaces_when_nothing_recovers():
    events = await _run(
        [
            _failed_tool("integral_query"),
            _failed_tool("integral_list_tracks"),
            _final(None),
        ]
    )
    messages = " ".join(e["message"] for e in events if e.get("type") == "error")
    assert "integral_query" in messages
    assert "integral_list_tracks" in messages


@pytest.mark.asyncio
async def test_clean_turn_emits_no_error():
    events = await _run([_final("All good.")])
    assert [e for e in events if e.get("type") == "error"] == []
