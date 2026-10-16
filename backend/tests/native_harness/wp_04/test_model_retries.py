"""Bounded physical model retries preserve admission and turn identity."""

import asyncio

import httpx
import pytest

from app.agentive.harness.litellm_model import LiteLLMSDKTransport
from app.agentive.harness.model_errors import NativeModelRequestError
from app.services.model_retry_status import (
    emit_model_retry_status,
    stream_with_retry_status,
)
from tests.native_harness.wp_04.test_litellm_sdk_transport import _route, _scope


def request(stream=False):
    return httpx.Request(
        "POST",
        "http://bridge/v1/chat/completions",
        json={
            "model": _route().model,
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": stream,
        },
    )


def reply():
    return {
        "id": "reply",
        "choices": [{"message": {"role": "assistant", "content": "Hello"}}],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "model_request_timeout",
        "model_rate_limited",
        "model_provider_unavailable",
        "model_empty_response",
    ],
)
async def test_transient_failure_retries_with_fresh_attempt_admission(failure):
    observations, admissions, statuses = [], [], []

    async def observe(item):
        observations.append(item)

    async def admission(item):
        admissions.append(item.request_id)

        async def ready():
            return None

        return ready

    calls = 0

    async def completion(**kwargs):
        nonlocal calls
        calls += 1
        assert kwargs["num_retries"] == 0
        if calls == 1:
            raise NativeModelRequestError(failure)
        return reply()

    async def status(attempt, maximum):
        statuses.append((attempt, maximum))

    transport = LiteLLMSDKTransport(
        route=_route(),
        scope=_scope(),
        observer=observe,
        admission=admission,
        completion=completion,
        max_attempts=3,
        retry_status=status,
    )
    response = await transport.handle_async_request(request())
    assert response.status_code == 200 and calls == 2
    assert statuses == [(2, 3), (0, 3)]
    assert len(set(admissions)) == 2
    assert [o.attempt for o in observations] == [1, 1, 2, 2]
    assert all(o.scope.run_id == _scope().run_id for o in observations)
    assert observations[-1].outcome == "responded"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,expected",
    [
        ("model_request_timeout", 3),
        ("model_authentication_failed", 1),
        ("model_context_limit", 1),
    ],
)
async def test_retry_cap_and_permanent_errors(code, expected):
    calls = 0

    async def completion(**kwargs):
        nonlocal calls
        calls += 1
        raise NativeModelRequestError(code)

    async def observe(item):
        return None

    transport = LiteLLMSDKTransport(
        route=_route(),
        scope=_scope(),
        observer=observe,
        completion=completion,
        max_attempts=3,
    )
    with pytest.raises(NativeModelRequestError):
        await transport.handle_async_request(request())
    assert calls == expected


@pytest.mark.asyncio
async def test_retry_stops_if_current_admission_is_revoked():
    admissions = calls = 0

    async def admission(item):
        nonlocal admissions
        admissions += 1
        if admissions == 2:
            raise PermissionError("Revoked")

        async def ready():
            return None

        return ready

    async def completion(**kwargs):
        nonlocal calls
        calls += 1
        raise NativeModelRequestError("model_request_timeout")

    async def observe(item):
        return None

    transport = LiteLLMSDKTransport(
        route=_route(),
        scope=_scope(),
        observer=observe,
        admission=admission,
        completion=completion,
        max_attempts=3,
    )
    with pytest.raises(PermissionError):
        await transport.handle_async_request(request())
    assert calls == 1 and admissions == 2


@pytest.mark.asyncio
async def test_stream_failure_before_first_chunk_retries():
    calls = 0
    observations = []

    async def completion(**kwargs):
        nonlocal calls
        calls += 1
        attempt = calls

        async def chunks():
            if attempt == 1:
                raise httpx.ReadTimeout("private detail")
            yield {
                "id": "reply",
                "choices": [{"delta": {"content": "Hello"}, "finish_reason": "stop"}],
            }

        return chunks()

    async def observe(item):
        observations.append(item)

    transport = LiteLLMSDKTransport(
        route=_route(),
        scope=_scope(),
        observer=observe,
        completion=completion,
        max_attempts=3,
    )
    response = await transport.handle_async_request(request(True))
    content = await response.aread()
    assert b"Hello" in content and calls == 2
    assert observations[-1].attempt == 2


@pytest.mark.asyncio
async def test_retry_status_is_live_and_cancellation_closes_producer():
    closed = asyncio.Event()

    async def source():
        try:
            await emit_model_retry_status(2, 3)
            await asyncio.Event().wait()
            yield {"type": "message-finish"}
        finally:
            closed.set()

    stream = stream_with_retry_status(source())
    assert await anext(stream) == {"type": "status", "text": "Retrying · 2 of 3"}
    await stream.aclose()
    assert closed.is_set()


@pytest.mark.asyncio
async def test_empty_reply_exhausts_three_attempts():
    calls = 0

    async def completion(**kwargs):
        nonlocal calls
        calls += 1
        return {"choices": [{"message": {"content": None}}]}

    async def observe(item):
        return None

    transport = LiteLLMSDKTransport(
        route=_route(),
        scope=_scope(),
        observer=observe,
        completion=completion,
        max_attempts=3,
    )
    with pytest.raises(NativeModelRequestError) as caught:
        await transport.handle_async_request(request())
    assert caught.value.code == "model_empty_response" and calls == 3


@pytest.mark.asyncio
async def test_stream_failure_after_output_does_not_restart():
    calls = 0

    async def completion(**kwargs):
        nonlocal calls
        calls += 1

        async def chunks():
            yield {"id": "reply", "choices": [{"delta": {"content": "Partial"}}]}
            raise httpx.ReadTimeout("private detail")

        return chunks()

    async def observe(item):
        return None

    transport = LiteLLMSDKTransport(
        route=_route(),
        scope=_scope(),
        observer=observe,
        completion=completion,
        max_attempts=3,
    )
    response = await transport.handle_async_request(request(True))
    with pytest.raises(Exception):
        await response.aread()
    assert calls == 1


@pytest.mark.asyncio
async def test_cancel_during_backoff_does_not_dispatch_again():
    retrying = asyncio.Event()
    calls = 0

    async def completion(**kwargs):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("private detail")

    async def observe(item):
        return None

    async def status(attempt, maximum):
        retrying.set()

    transport = LiteLLMSDKTransport(
        route=_route(),
        scope=_scope(),
        observer=observe,
        completion=completion,
        max_attempts=3,
        retry_status=status,
    )
    task = asyncio.create_task(transport.handle_async_request(request()))
    await retrying.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == 1


@pytest.mark.asyncio
async def test_provider_task_cancellation_reaches_consumer():
    async def source():
        await emit_model_retry_status(2, 3)
        raise asyncio.CancelledError
        yield {}

    stream = stream_with_retry_status(source())
    assert (await anext(stream))["text"] == "Retrying · 2 of 3"
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(anext(stream), timeout=1)
