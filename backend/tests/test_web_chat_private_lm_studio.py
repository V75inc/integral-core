"""An opt-in local model port, exercised only through an in-process transport."""

import asyncio
import json

import httpx
import pytest

from app.agentive.services.web_chat_profile_inference import WebChatModelInput
from app.web import web_chat_private_lm_studio as adapter

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
MODEL = "fixture/tiny-chat@q4"
MESSAGE = "A synthetic test question."
PERSONA = "Answer clearly and concisely."
PROMPT = WebChatModelInput(
    "Not passed through to the local model", PERSONA, (), MESSAGE
)
REPLY = "A synthetic text reply."
UNAVAILABLE = "The private Web Chat test model is unavailable."


def _body(*, reply=REPLY, model=MODEL, role="assistant", reason="stop", **extra):
    return {
        "model": model,
        "choices": [
            {
                "message": {"role": role, "content": reply, **extra},
                "finish_reason": reason,
            }
        ],
    }


def _build(handler, model=MODEL):
    return adapter._build_private_lm_studio_text_model(
        model, transport=httpx.MockTransport(handler)
    )


@pytest.mark.parametrize(
    "value", ["", None, " fixture", "https://fake.invalid", "a" * 129]
)
async def test_factory_requires_an_exact_pinned_chat_model(value):
    with pytest.raises(ValueError, match="exact LM Studio"):
        adapter.build_private_lm_studio_text_model(value)


async def test_public_factory_is_inert_until_called_then_uses_literal_loopback_factory(
    monkeypatch,
):
    options = []
    requests = []

    async def respond(request):
        requests.append(request)
        return httpx.Response(200, json=_body())

    # Exercise the production factory and options without constructing a real
    # network transport or contacting any listener.
    def substitute_transport(**kwargs):
        options.append(kwargs)
        return httpx.MockTransport(respond)

    monkeypatch.setattr(adapter.httpx, "AsyncHTTPTransport", substitute_transport)
    complete = adapter.build_private_lm_studio_text_model(MODEL)
    assert options == requests == []
    assert await complete(PROMPT) == REPLY
    assert options == [{"local_address": "127.0.0.1", "retries": 0, "trust_env": False}]
    [request] = requests
    assert str(request.url) == "http://127.0.0.1:1234/v1/chat/completions"


async def test_pinned_single_turn_has_no_credentials_context_or_tools(monkeypatch):
    requests = []
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-should-not-be-used")
    monkeypatch.setenv("HTTP_PROXY", "http://synthetic-proxy.invalid")

    async def respond(request):
        requests.append(request)
        assert str(request.url) == "http://127.0.0.1:1234/v1/chat/completions"
        assert request.method == "POST"
        assert request.headers["accept"] == "application/json"
        assert request.headers["accept-encoding"] == "identity"
        for name in ("Authorization", "Proxy-Authorization", "Cookie"):
            assert name not in request.headers
        shape = json.loads(request.content)
        assert shape == {
            "model": MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": f"{adapter._SYSTEM}\n\nPublished persona:\n{PERSONA}",
                },
                {"role": "user", "content": MESSAGE},
            ],
            "max_tokens": 256,
            "stream": False,
        }
        return httpx.Response(200, json=_body(reasoning_content="not shown"))

    complete = _build(respond)
    assert await complete(PROMPT) == REPLY
    assert len(requests) == 1


@pytest.mark.parametrize(
    "prompt",
    [
        WebChatModelInput("", PERSONA, (("old prompt", "old answer"),), MESSAGE),
        WebChatModelInput("", PERSONA, (), "x" * 1001),
        WebChatModelInput("", "x" * 3001, (), MESSAGE),
        WebChatModelInput("", PERSONA, (), " "),
        WebChatModelInput("", PERSONA, (), "A forbidden control\x00"),
        {"visitor_text": "phantom"},
    ],
)
async def test_invalid_input_does_not_contact_transport(prompt):
    async def never(_):
        raise AssertionError("The model must not see invalid input")

    with pytest.raises(adapter.PrivateLMStudioUnavailable, match=UNAVAILABLE):
        await _build(never)(prompt)


@pytest.mark.parametrize(
    "payload",
    [
        _body(model="different-model"),
        _body(role="tool"),
        _body(reason="tool_calls"),
        _body(reason="length"),
        _body(tool_calls=[{"id": "fixture", "type": "function"}]),
        _body(function_call={"name": "fixture"}),
        _body(reply=[{"type": "text", "text": "not plain text"}]),
        _body(reply=" "),
        _body(reply="partial\x00text"),
        _body(reply="x" * 4001),
        {"model": MODEL, "choices": []},
        {"model": MODEL, "choices": [None, None]},
    ],
)
async def test_bad_model_output_fails_without_rendering_it(payload):
    async def respond(_):
        return httpx.Response(200, json=payload)

    with pytest.raises(adapter.PrivateLMStudioUnavailable) as error:
        await _build(respond)(PROMPT)
    assert str(error.value) == UNAVAILABLE


@pytest.mark.parametrize(
    "status, headers, data",
    [
        (302, {"Location": "http://invalid.example/never"}, b"redirect"),
        (401, {"Content-Type": "application/json"}, b"private response"),
        (200, {"Content-Type": "text/html"}, b"not json"),
        (
            200,
            {"Content-Type": "application/json", "Content-Encoding": "gzip"},
            b"encoded",
        ),
        (
            200,
            {"Content-Type": "application/json", "Content-Length": "99999999"},
            b"too large",
        ),
        (200, {"Content-Type": "application/json", "Content-Length": "5"}, b"{}"),
        (200, {"Content-Type": "application/json"}, b'{"model":0,"model":1}'),
        (200, {"Content-Type": "application/json"}, b'{"invalid":NaN}'),
        (200, {"Content-Type": "application/json"}, b"\xff"),
    ],
)
async def test_transport_error_is_generic_and_never_retries(status, headers, data):
    requests = []

    async def respond(request):
        requests.append(request)
        return httpx.Response(status, headers=headers, content=data)

    with pytest.raises(adapter.PrivateLMStudioUnavailable) as error:
        await _build(respond)(PROMPT)
    assert str(error.value) == UNAVAILABLE
    assert len(requests) == 1


async def test_wire_buffer_is_bounded_and_closed_without_draining():
    class ResponseStream(httpx.AsyncByteStream):
        parts = 0
        closed = False

        async def __aiter__(self):
            for _ in range(60):
                self.parts += 1
                yield b"x" * 4096

        async def aclose(self):
            self.closed = True

    stream = ResponseStream()

    async def respond(_):
        return httpx.Response(
            200, headers={"Content-Type": "application/json"}, stream=stream
        )

    with pytest.raises(adapter.PrivateLMStudioUnavailable, match=UNAVAILABLE):
        await _build(respond)(PROMPT)
    assert stream.parts == 9
    assert stream.closed


async def test_single_pending_call_deadline_and_subsequent_recovery(monkeypatch):
    entered = asyncio.Event()
    never = asyncio.Event()
    calls = 0

    async def respond(_):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await never.wait()
        return httpx.Response(200, json=_body())

    monkeypatch.setattr(adapter, "_DEADLINE_SECONDS", 0.05)
    complete = _build(respond)
    first = asyncio.create_task(complete(PROMPT))
    await asyncio.wait_for(entered.wait(), 1)
    with pytest.raises(adapter.PrivateLMStudioUnavailable, match=UNAVAILABLE):
        await complete(PROMPT)
    with pytest.raises(adapter.PrivateLMStudioUnavailable, match=UNAVAILABLE):
        await asyncio.wait_for(first, 1)
    assert calls == 1
    assert await complete(PROMPT) == REPLY
    assert calls == 2


async def test_transport_failure_does_not_leak_provider_details():
    async def broken(_):
        raise httpx.ConnectError("synthetic internal exception detail")

    with pytest.raises(adapter.PrivateLMStudioUnavailable) as error:
        await _build(broken)(PROMPT)
    assert str(error.value) == UNAVAILABLE
