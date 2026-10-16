"""Offline security contracts for user-configurable model endpoints."""

import asyncio
import json
import socket
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import custom_model_transport as transport
from app.services import url_safety
from app.services.model_provider_endpoints import normalize_custom_base_url

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]


@pytest.fixture
def public_dns(monkeypatch):
    monkeypatch.setattr(url_safety, "_pinned_hosts", {})
    monkeypatch.setenv("INTEGRAL_OUTBOUND_TRUSTED_HOSTS", "")
    monkeypatch.setenv("INTEGRAL_OUTBOUND_EXTRA_PORTS", "")
    monkeypatch.setattr(url_safety, "_resolve_host_ips", lambda host: ["8.8.8.8"])
    monkeypatch.setattr(
        url_safety,
        "_real_getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
    )


def mock_network(monkeypatch, handler):
    original = transport.CustomModelAsyncClient
    monkeypatch.setattr(
        transport,
        "CustomModelAsyncClient",
        lambda base, **kw: original(base, transport=httpx.MockTransport(handler), **kw),
    )


@pytest.mark.parametrize(
    "base",
    [
        "https://127.0.0.1/v1",
        "https://[::1]/v1",
        "https://[::ffff:127.0.0.1]/v1",
        "https://169.254.169.254/v1",
        "https://10.0.0.1/v1",
        "https://192.168.1.2/v1",
        "https://localhost/v1",
        "https://metadata.google.internal/v1",
        "https://llm.example:6379/v1",
        "http://llm.example/v1",
    ],
)
async def test_untrusted_endpoint_is_rejected_before_request(
    base, public_dns, monkeypatch
):
    remote = AsyncMock()
    mock_network(monkeypatch, remote)
    ok, _ = await transport.probe_custom_model_endpoint(base, "synthetic-key")
    assert not ok
    remote.assert_not_awaited()


@pytest.mark.parametrize(
    "base",
    [
        "https://user:password@example.com/v1",
        "https:///v1",
        "file:///tmp/model",
        "https://example.com/v1?api_key=hidden",
        "https://example.com/v1#hidden",
        "https://example.com:bad/v1",
        "https://example.com/\\evil/v1",
    ],
)
async def test_endpoint_identity_rejects_ambiguous_or_secret_urls(base):
    with pytest.raises(ValueError):
        normalize_custom_base_url(base)


async def test_endpoint_identity_normalizes_host_port_and_trailing_slash():
    assert (
        normalize_custom_base_url("HTTPS://LLM.EXAMPLE:443/v1/")
        == "https://llm.example/v1"
    )


async def test_dns_rebinding_is_rejected_at_connection(public_dns, monkeypatch):
    monkeypatch.setattr(
        url_safety,
        "_real_getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ],
    )
    remote = AsyncMock()
    mock_network(monkeypatch, remote)
    assert not (
        await transport.probe_custom_model_endpoint("https://llm.example/v1", "")
    )[0]
    remote.assert_not_awaited()


async def test_failed_pin_does_not_fall_back_to_a_second_dns_resolution(
    public_dns, monkeypatch
):
    monkeypatch.setattr(url_safety, "_resolve_pinned_records", lambda host: None)
    remote = AsyncMock()
    mock_network(monkeypatch, remote)
    assert not (
        await transport.probe_custom_model_endpoint("https://llm.example/v1", "")
    )[0]
    remote.assert_not_awaited()


async def test_operator_can_trust_exact_internal_model_host_and_port(
    public_dns, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_OUTBOUND_TRUSTED_HOSTS", "model.internal")
    monkeypatch.setenv("INTEGRAL_OUTBOUND_EXTRA_PORTS", "8000")
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": []})

    mock_network(monkeypatch, handler)
    assert (
        await transport.probe_custom_model_endpoint("http://model.internal:8000/v1", "")
    )[0]
    assert len(seen) == 1
    assert "authorization" not in seen[0].headers
    assert "model.internal" not in url_safety._pinned_hosts


async def test_redirect_never_forwards_key_to_another_authority(
    public_dns, monkeypatch
):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            307, headers={"location": "https://another.example/models"}
        )

    mock_network(monkeypatch, handler)
    assert not (
        await transport.probe_custom_model_endpoint(
            "https://llm.example/v1", "synthetic-key"
        )
    )[0]
    assert len(seen) == 1 and seen[0].url.host == "llm.example"


async def test_probe_requires_models_json_and_bounds_body(public_dns, monkeypatch):
    for content in [
        b"<html>ok</html>",
        b"{}",
        b"x" * (transport.MAX_MODEL_PROBE_BYTES + 1),
    ]:

        def handler(request, content=content):
            return httpx.Response(200, content=content)

        # Keep one real class across parameter iterations.
        with monkeypatch.context() as context:
            mock_network(context, handler)
            assert not (
                await transport.probe_custom_model_endpoint(
                    "https://llm.example/v1", ""
                )
            )[0]


async def test_probe_does_not_log_untrusted_error_body(public_dns, monkeypatch, caplog):
    mock_network(
        monkeypatch,
        lambda req: httpx.Response(500, text="synthetic-secret-in-response"),
    )
    assert not (
        await transport.probe_custom_model_endpoint(
            "https://llm.example/v1", "synthetic-secret-in-response"
        )
    )[0]
    assert "synthetic-secret-in-response" not in caplog.text


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("keyless", [False, True])
async def test_actual_litellm_dispatch_uses_guarded_client(
    public_dns, monkeypatch, stream, keyless
):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", "/private/tmp/integral-pr138-token-cache")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-deployment-key")
    monkeypatch.setenv("OPENAI_ORG_ID", "synthetic-deployment-org")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "synthetic-deployment-project")
    import litellm

    seen = []

    def handler(request):
        seen.append(request)
        if keyless:
            assert "authorization" not in request.headers
        else:
            assert request.headers["authorization"] == "Bearer synthetic-user-key"
        assert not request.headers.get("openai-organization")
        assert not request.headers.get("openai-project")
        body = {
            "id": "completion-1",
            "object": "chat.completion",
            "created": 1,
            "model": "fixture-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
        }
        if stream:
            body["object"] = "chat.completion.chunk"
            body["choices"][0]["delta"] = body["choices"][0].pop("message")
            content = "data: " + json.dumps(body) + "\n\ndata: [DONE]\n\n"
            return httpx.Response(
                200, text=content, headers={"content-type": "text/event-stream"}
            )
        return httpx.Response(200, json=body)

    mock_network(monkeypatch, handler)
    result = await transport.custom_endpoint_completion(
        litellm.acompletion,
        {
            "model": "openai/fixture-model",
            "api_base": "https://llm.example/v1",
            "api_key": "integral-keyless-endpoint" if keyless else "synthetic-user-key",
            "timeout": 5.0,
            "num_retries": 0,
            "stream": stream,
            "messages": [{"role": "user", "content": "hello"}],
        },
        keyless_endpoint=keyless,
    )
    if stream:
        chunks = [chunk async for chunk in result]
        assert chunks
        await result.aclose()
    else:
        assert result.choices[0].message.content == "ok"
    assert (
        len(seen) == 1 and str(seen[0].url) == "https://llm.example/v1/chat/completions"
    )
    assert "llm.example" not in url_safety._pinned_hosts


async def test_stream_close_releases_client_without_consuming_stream():
    client = AsyncMock()

    async def chunks():
        yield "hello"

    result = transport._OwnedModelStream(chunks(), client)
    await result.aclose()
    client.close.assert_awaited_once()


async def test_completion_cancellation_closes_client(public_dns, monkeypatch):
    original = transport.CustomModelAsyncClient
    clients = []

    def factory(*a, **kw):
        result = original(*a, **kw)
        clients.append(result)
        return result

    monkeypatch.setattr(transport, "CustomModelAsyncClient", factory)

    async def cancelled(**kwargs):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await transport.custom_endpoint_completion(
            cancelled,
            {
                "api_base": "https://llm.example/v1",
                "api_key": "synthetic",
                "timeout": 5,
            },
        )
    assert clients and clients[0].is_closed


@pytest.mark.parametrize(
    "key,status,expected",
    [
        ("", 401, "endpoint requires an API key"),
        ("", 403, "endpoint requires an API key"),
        ("synthetic-key", 401, "invalid API key"),
    ],
)
async def test_authentication_status_is_actionable(
    public_dns, monkeypatch, key, status, expected
):
    mock_network(monkeypatch, lambda req: httpx.Response(status))
    assert await transport.probe_custom_model_endpoint(
        "https://llm.example/v1", key
    ) == (False, expected)


async def test_probe_has_a_total_deadline(public_dns, monkeypatch):
    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(1)
            yield b'{"data": []}'

    monkeypatch.setattr(transport, "MODEL_PROBE_TIMEOUT_SECONDS", 0.02)
    mock_network(monkeypatch, lambda request: httpx.Response(200, stream=SlowBody()))
    result = await asyncio.wait_for(
        transport.probe_custom_model_endpoint("https://llm.example/v1", ""), 0.5
    )
    assert result[0] is False
    assert "llm.example" not in url_safety._pinned_hosts


async def test_connection_rejects_request_outside_endpoint(public_dns):
    seen = []
    async with transport.CustomModelAsyncClient(
        "https://llm.example/v1",
        timeout=1,
        transport=httpx.MockTransport(lambda req: seen.append(req)),
    ) as client:
        with pytest.raises(ValueError, match="left its configured endpoint"):
            await client.get("https://other.example/v1/models")
    assert seen == []


@pytest.mark.parametrize("host", ["2130706433", "0x7f000001", "0177.0.0.1"])
async def test_encoded_private_addresses_are_checked_after_resolution(
    public_dns, monkeypatch, host
):
    monkeypatch.setattr(url_safety, "_resolve_host_ips", lambda hostname: ["127.0.0.1"])
    remote = AsyncMock()
    mock_network(monkeypatch, remote)
    assert not (await transport.probe_custom_model_endpoint(f"https://{host}/v1", ""))[
        0
    ]
    remote.assert_not_awaited()


async def test_owned_stream_preserves_provider_accounting_metadata():
    from app.agentive.harness.litellm_model import _LiteLLMStream

    class ProviderStream:
        _hidden_params = {
            "response_cost": 0.25,
            "additional_headers": {"x-request-id": "provider-request"},
        }

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

        async def aclose(self):
            pass

    owned = transport._OwnedModelStream(ProviderStream(), AsyncMock())
    finish = AsyncMock()
    stream = _LiteLLMStream(chunks=owned, response=owned, finish=finish)
    async for _ in stream:
        pass
    response = finish.await_args.args[0]
    assert response._hidden_params["response_cost"] == 0.25
    assert (
        response._hidden_params["additional_headers"]["x-request-id"]
        == "provider-request"
    )


@pytest.mark.parametrize("redirect", [False, True])
async def test_inference_enforces_network_policy_not_only_probe(
    public_dns, monkeypatch, redirect
):
    from openai import APIConnectionError, APIStatusError

    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            307, headers={"location": "https://other.example/v1/chat/completions"}
        )

    mock_network(monkeypatch, handler)

    async def completion(**kwargs):
        return await kwargs["client"].chat.completions.create(
            model="fixture", messages=[{"role": "user", "content": "hello"}]
        )

    base = "https://llm.example/v1" if redirect else "https://127.0.0.1/v1"
    with pytest.raises((APIConnectionError, APIStatusError)):
        await transport.custom_endpoint_completion(
            completion,
            {
                "api_base": base,
                "api_key": "synthetic-user-key",
                "timeout": 1,
            },
        )
    assert len(seen) == (1 if redirect else 0)
    assert "llm.example" not in url_safety._pinned_hosts


async def test_key_equal_to_sdk_sentinel_is_not_implicitly_keyless(
    public_dns, monkeypatch
):
    from app.services.model_provider_endpoints import KEYLESS_PLACEHOLDER_API_KEY

    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "id": "reply",
                "object": "chat.completion",
                "created": 1,
                "model": "fixture",
                "choices": [],
            },
        )

    mock_network(monkeypatch, handler)

    async def completion(**kwargs):
        return await kwargs["client"].chat.completions.create(
            model="fixture", messages=[]
        )

    await transport.custom_endpoint_completion(
        completion,
        {
            "api_base": "https://llm.example/v1",
            "api_key": KEYLESS_PLACEHOLDER_API_KEY,
            "timeout": 1,
        },
        keyless_endpoint=False,
    )
    assert seen[0].headers["authorization"] == f"Bearer {KEYLESS_PLACEHOLDER_API_KEY}"


async def test_compressed_probe_response_is_rejected_without_decoding(
    public_dns, monkeypatch
):
    import gzip

    reads = []

    class CompressedBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            reads.append(True)
            yield gzip.compress(b"x" * 1000000)

    def handler(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            200, headers={"content-encoding": "gzip"}, stream=CompressedBody()
        )

    mock_network(monkeypatch, handler)
    assert not (
        await transport.probe_custom_model_endpoint("https://llm.example/v1", "")
    )[0]
    assert reads == []


async def test_raw_probe_body_limit_closes_stream(public_dns, monkeypatch):
    closed = []

    class LargeBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(20):
                yield b"x" * 8192

        async def aclose(self):
            closed.append(True)

    mock_network(monkeypatch, lambda request: httpx.Response(200, stream=LargeBody()))
    assert not (
        await transport.probe_custom_model_endpoint("https://llm.example/v1", "")
    )[0]
    assert closed
    assert "llm.example" not in url_safety._pinned_hosts
