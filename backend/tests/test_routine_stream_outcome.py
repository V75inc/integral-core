"""Routine success follows settled execution, not HTTP success or stream EOF."""

from types import SimpleNamespace

import pytest

from app.services.chat_sse import settled_chat_turn_outcome, sse_bytes

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]


async def _chunks(*chunks):
    for chunk in chunks:
        yield chunk


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled"])
async def test_settled_status_controls_headless_outcome(status):
    result = await settled_chat_turn_outcome(
        _chunks(sse_bytes("turn-settled", {"status": status}))
    )
    assert result[0] is (status == "succeeded")
    assert (result[1] is None) is (status == "succeeded")


@pytest.mark.parametrize("style", ["bytes", "text", "fragmented", "crlf", "multiline"])
async def test_stream_framing_preserves_unicode_and_one_settlement(style):
    stream = sse_bytes("text-delta", {"type": "text-delta", "delta": "Résumé ✓"})
    stream += sse_bytes("turn-settled", {"type": "turn-settled", "status": "succeeded"})
    if style == "text":
        chunks = [stream.decode()]
    elif style == "fragmented":
        chunks = [stream[i : i + 1] for i in range(len(stream))]
    elif style == "crlf":
        stream = stream.replace(b"\n", b"\r\n")
        chunks = [stream[i : i + 1] for i in range(len(stream))]
    elif style == "multiline":
        chunks = [
            b': keepalive\n\nevent: turn-settled\ndata: {\ndata: "status": "succeeded"}\n\n'
        ]
    else:
        chunks = [stream]
    assert await settled_chat_turn_outcome(_chunks(*chunks)) == (True, None)


async def test_error_event_prevents_even_a_reported_success():
    result = await settled_chat_turn_outcome(
        _chunks(
            sse_bytes(
                "error", {"code": "quota_exhausted", "message": "Allowance exhausted."}
            ),
            sse_bytes("turn-settled", {"status": "succeeded"}),
        )
    )
    assert result == (False, "Allowance exhausted.")


@pytest.mark.parametrize("frames", [[], [sse_bytes("routine-no-message", {})]])
async def test_eof_and_quiet_signal_do_not_establish_completion(frames):
    assert (await settled_chat_turn_outcome(_chunks(*frames)))[0] is False


async def test_quiet_success_requires_its_settled_acknowledgement():
    assert await settled_chat_turn_outcome(
        _chunks(
            sse_bytes("routine-no-message", {"type": "routine-no-message"}),
            sse_bytes("turn-settled", {"status": "succeeded"}),
        )
    ) == (True, None)


@pytest.mark.parametrize(
    "stream",
    [
        b'event: turn-settled\ndata: {"status": "succeeded"}',
        b"event: turn-settled\ndata: invalid\n\n",
        b"event: turn-settled\ndata: []\n\n",
        b"event: turn-settled\n\n",
        sse_bytes("turn-settled", {"status": "unknown"}),
        sse_bytes("turn-settled", {"status": []}),
        sse_bytes("turn-settled", {"status": "succeeded"}) * 2,
        sse_bytes("turn-settled", {"status": "succeeded"})
        + sse_bytes("text-delta", {"delta": "late"}),
        b'data: "\xff"\n\n',
    ],
)
async def test_invalid_or_ambiguous_settlement_is_refused(stream):
    with pytest.raises((ValueError, UnicodeError)):
        await settled_chat_turn_outcome(_chunks(stream))


async def test_event_frame_bound_is_enforced():
    with pytest.raises(ValueError, match="bound"):
        await settled_chat_turn_outcome(
            _chunks(b"data: " + b"a" * 30), max_frame_chars=16
        )


@pytest.mark.parametrize("outcome", ["succeeded", "failed", "cancelled", "incomplete"])
async def test_routine_run_count_requires_successful_settled_stream(
    outcome, monkeypatch
):
    from app.agentive.nodes import RoutineTask
    from app.agentive.services.routine_tasks import create_routine_task
    from app.services import routine_task_scheduler as scheduler
    from tests.test_routine_tasks import _bootstrap_user_workspace_track_thread

    user_id, workspace_id, _, thread = await _bootstrap_user_workspace_track_thread(
        f"routine-outcome-{outcome}@example.com"
    )
    routine = await create_routine_task(
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        instruction="Read saved status only.",
        cron="0 9 * * *",
        max_runs=1,
    )
    closed = []

    async def stream():
        try:
            if outcome == "failed":
                yield sse_bytes(
                    "error", {"type": "error", "message": "Allowance exhausted."}
                )
            if outcome != "incomplete":
                yield sse_bytes("turn-settled", {"status": outcome})
        finally:
            closed.append(True)

    async def invoke(*args, **kwargs):
        return SimpleNamespace(body_iterator=stream())

    monkeypatch.setattr("app.agentive.tooling.invoke.invoke_route_in_process", invoke)
    # Exercise the real scheduler -> claimed worker -> real headless reader.
    # Only the API stream is replaced; there is no SDK/provider request.
    await scheduler._execute_run(routine)
    saved = await RoutineTask.get(routine.id)
    success = outcome == "succeeded"
    assert saved.run_count == (1 if success else 0)
    assert saved.last_run_status == ("success" if success else "error")
    assert saved.status == ("completed" if success else "active")
    assert closed == [True]
