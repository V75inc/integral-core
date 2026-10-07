"""Offline contract tests for the in-process LiteLLM SDK bridge."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import httpx
import pytest
from openai import APIConnectionError, AsyncOpenAI, BadRequestError
from pydantic_ai import Agent

from app.agentive.harness.contracts import HarnessExecutionScope, ResolvedModelRoute
from app.agentive.harness.litellm_model import (
    LiteLLMSDKTransport,
    _LiteLLMStream,
    _ModelStreamOutcome,
    _usage,
    build_litellm_sdk_model,
)


def _scope() -> HarnessExecutionScope:
    return HarnessExecutionScope(
        tenant_id="workspace-1",
        principal_id="user-1",
        workspace_id="workspace-1",
        thread_id="thread-1",
        session_id="session-1",
        run_id="run-1",
        permission_revision="permissions-1",
        capability_version="tools-1",
    )


def _route() -> ResolvedModelRoute:
    return ResolvedModelRoute(
        provider="anthropic",
        model="anthropic/claude-sonnet",
        api_key="workspace-key-test",
        credential_source="workspace_byok",
        credential_ref="credential-1",
    )


async def _ignore_observation(_observation: Any) -> None:
    """Accept offline lifecycle notifications in transport-only fixtures."""
    return None


def test_usage_calculates_cost_when_litellm_returns_tokens_without_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Known LiteLLM model pricing fills the cost omitted from response usage."""
    # LiteLLM initializes its tokenizer cache path on import. Keep that
    # process-level setting contained by pytest's environment restoration.
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", "/tmp/integral-litellm-test-cache")
    import litellm

    response = {
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "total_tokens": 110,
        }
    }
    captured: dict[str, Any] = {}

    def calculate(**kwargs: Any) -> float:
        captured.update(kwargs)
        return 0.000056

    monkeypatch.setattr(
        litellm,
        "get_model_info",
        lambda **_kwargs: {
            "input_cost_per_token": 0.0000004,
            "output_cost_per_token": 0.0000016,
        },
    )
    monkeypatch.setattr(litellm, "completion_cost", calculate)

    usage = _usage(
        response,
        complete=True,
        model="openai/gpt-4.1-mini",
        provider="openai",
    )

    assert usage.provider_cost_usd == Decimal("0.000056")
    assert usage.cost_source == "litellm_calculated"
    assert usage.complete is True
    assert captured["completion_response"] is response
    assert captured["model"] == "openai/gpt-4.1-mini"
    assert captured["custom_llm_provider"] == "openai"


def test_usage_does_not_invent_cost_for_an_unpriced_litellm_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown local/custom pricing stays unavailable instead of becoming zero."""
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", "/tmp/integral-litellm-test-cache")
    import litellm

    monkeypatch.setattr(
        litellm,
        "get_model_info",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("model is unmapped")),
    )
    response = {
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "total_tokens": 110,
        }
    }

    usage = _usage(
        response,
        complete=True,
        model="ollama_chat/custom-local-model",
        provider="ollama_chat",
    )

    assert usage.provider_cost_usd is None
    assert usage.cost_source == "unavailable"
    assert usage.complete is True


def test_usage_treats_zero_rate_litellm_mapping_as_unpriced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A zero-filled model map must not masquerade as a free cloud route."""
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", "/tmp/integral-litellm-test-cache")
    import litellm

    monkeypatch.setattr(
        litellm,
        "get_model_info",
        lambda **_kwargs: {
            "input_cost_per_token": 0.0,
            "output_cost_per_token": 0.0,
        },
    )
    monkeypatch.setattr(
        litellm,
        "completion_cost",
        lambda **_kwargs: pytest.fail("must not price a zero-filled model map"),
    )
    usage = _usage(
        {
            "usage": {"prompt_tokens": 100, "completion_tokens": 10},
        },
        complete=True,
        model="ollama_chat/glm-5.3:cloud",
        provider="ollama_chat",
    )

    assert usage.provider_cost_usd is None
    assert usage.cost_source == "unavailable"


def test_usage_preserves_explicit_provider_zero_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicitly reported zero is data and takes precedence over pricing."""
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", "/tmp/integral-litellm-test-cache")
    import litellm

    monkeypatch.setattr(
        litellm,
        "completion_cost",
        lambda **_kwargs: pytest.fail("explicit provider cost takes precedence"),
    )
    usage = _usage(
        {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "cost": 0,
            },
        },
        complete=True,
        model="openai/gpt-4.1-mini",
        provider="openai",
    )

    assert usage.provider_cost_usd == Decimal("0")
    assert usage.cost_source == "provider_response"


def test_usage_keeps_sdk_zero_without_claiming_an_unpriced_route_is_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import litellm

    class Response:
        _hidden_params = {"response_cost": 0.0}

        def model_dump(self, **_kwargs):
            return {"usage": {"prompt_tokens": 100, "completion_tokens": 10}}

    monkeypatch.setattr(
        litellm,
        "get_model_info",
        lambda **_kwargs: {
            "input_cost_per_token": 0.0,
            "output_cost_per_token": 0.0,
        },
    )
    usage = _usage(
        Response(),
        complete=True,
        model="ollama_chat/glm-5.3:cloud",
        provider="ollama_chat",
    )
    assert usage.input_tokens == 100
    assert usage.output_tokens == 10
    assert usage.litellm_response_cost_usd == Decimal("0.0")
    assert usage.provider_cost_usd is None
    assert usage.cost_source == "unavailable"


def test_usage_prefers_direct_provider_cost_over_litellm_hidden_cost() -> None:
    """Provider response amounts outrank LiteLLM's wrapper accounting field."""

    class Response:
        _hidden_params = {
            "response_cost": 0.004,
            "additional_headers": {
                "llm_provider-x-litellm-response-cost": 0.005,
            },
        }

        def model_dump(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "usage": {"prompt_tokens": 10, "completion_tokens": 1},
                "response_cost": 0.003,
            }

    usage = _usage(
        Response(),
        complete=True,
        model="openai/gpt-4.1-mini",
        provider="openai",
    )

    assert usage.provider_cost_usd == Decimal("0.003")
    assert usage.cost_source == "provider_response"


@pytest.mark.asyncio
async def test_bridge_routes_chat_completion_and_records_usage_without_network() -> (
    None
):
    """OpenAI-compatible Pydantic client traffic is routed through fake SDK."""
    captured: dict[str, Any] = {}
    observations = []

    async def completion(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "id": "provider-request-1",
            "object": "chat.completion",
            "created": 1,
            "model": "claude-sonnet",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "hello"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 5,
                "total_tokens": 17,
                "prompt_tokens_details": {"cached_tokens": 2},
                "completion_tokens_details": {"reasoning_tokens": 1},
                "cost": 0.002,
            },
        }

    async def observe(item: Any) -> None:
        observations.append(item)

    transport = LiteLLMSDKTransport(
        route=_route(), scope=_scope(), observer=observe, completion=completion
    )
    async with AsyncOpenAI(
        api_key="bridge-placeholder",
        base_url="http://litellm-sdk.invalid/v1",
        http_client=httpx.AsyncClient(transport=transport),
        max_retries=0,
    ) as client:
        result = await client.chat.completions.create(
            model="anthropic/claude-sonnet",
            messages=[{"role": "user", "content": "hello"}],
        )

    assert result.choices[0].message.content == "hello"
    assert captured["model"] == "anthropic/claude-sonnet"
    assert captured["api_key"] == "workspace-key-test"
    assert captured["num_retries"] == 0
    assert captured["timeout"] == 180.0
    assert [item.outcome for item in observations] == [
        "dispatch_intent",
        "responded",
    ]
    assert observations[1].provider_request_id == "provider-request-1"
    assert observations[1].usage.input_tokens == 12
    assert observations[1].usage.cached_input_tokens == 2
    assert observations[1].usage.reasoning_tokens == 1
    assert observations[1].usage.provider_cost_usd == Decimal("0.002")
    assert observations[1].scope == _scope()


@pytest.mark.asyncio
async def test_local_ollama_context_is_forwarded_as_provider_option() -> None:
    """The configured local context reaches LiteLLM without affecting others."""
    captured: dict[str, Any] = {}

    async def completion(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "id": "ollama-request-1",
            "object": "chat.completion",
            "created": 1,
            "model": "gemma4:26b",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "complete"},
                    "finish_reason": "stop",
                }
            ],
        }

    route = ResolvedModelRoute(
        provider="ollama_chat",
        model="ollama_chat/gemma4:26b",
        api_base="http://localhost:11434",
        ollama_num_ctx=32768,
        ollama_num_predict=8192,
        ollama_think="low",
        ollama_clear_thinking=True,
        credential_source="local",
    )
    model = build_litellm_sdk_model(
        route=route,
        scope=_scope(),
        observer=_ignore_observation,
        completion=completion,
        timeout_seconds=37,
    )
    result = await Agent(model).run(
        "return one word", model_settings={"max_tokens": 2048}
    )

    assert result.output == "complete"
    assert captured["extra_body"]["options"]["num_ctx"] == 32768
    assert captured["timeout"] == 37
    assert "num_ctx" not in captured
    assert captured["max_tokens"] == 8192
    assert "max_completion_tokens" not in captured
    assert captured["extra_body"]["think"] == "low"
    assert captured["extra_body"]["clear_thinking"] is True


def test_ollama_context_cannot_be_set_for_nonlocal_route() -> None:
    """Provider context overrides are limited to the local Ollama route."""
    with pytest.raises(ValueError, match="only for local Ollama routes"):
        ResolvedModelRoute(
            provider="openai",
            model="openai/gpt-4.1",
            ollama_num_ctx=8192,
            credential_source="platform",
        )


def test_ollama_thinking_controls_cannot_be_set_for_nonlocal_route() -> None:
    """Provider-specific controls cannot leak onto unrelated model routes."""
    with pytest.raises(ValueError, match="only for local Ollama routes"):
        ResolvedModelRoute(
            provider="openai",
            model="openai/gpt-4.1",
            ollama_think="low",
            credential_source="platform",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route_provider", "route_model"),
    [
        ("anthropic", "anthropic/claude-sonnet"),
        ("openai", "openai/gpt-4.1"),
    ],
)
async def test_pydantic_agent_runs_through_litellm_sdk_bridge(
    route_provider: str, route_model: str
) -> None:
    """The actual Pydantic model path reaches the injected LiteLLM SDK call."""
    calls = []

    async def completion(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "id": "provider-agent-1",
            "object": "chat.completion",
            "created": 1,
            "model": route_model.rsplit("/", 1)[-1],
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "native reply"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 14,
                "completion_tokens": 2,
                "total_tokens": 16,
            },
        }

    model = build_litellm_sdk_model(
        route=ResolvedModelRoute(
            provider=route_provider,
            model=route_model,
            api_key="workspace-key-test",
            credential_source="workspace_byok",
            credential_ref="credential-1",
        ),
        scope=_scope(),
        observer=_ignore_observation,
        completion=completion,
    )
    result = await Agent(model).run("return a short greeting")

    assert result.output == "native reply"
    assert len(calls) == 1
    assert calls[0]["model"] == route_model
    assert calls[0]["num_retries"] == 0
    assert calls[0]["timeout"] == 180.0
    assert "num_ctx" not in calls[0]
    assert "max_tokens" not in calls[0]


@pytest.mark.asyncio
async def test_bridge_preserves_tool_calls_in_litellm_response() -> None:
    """Tool-call JSON stays structured through SDK response serialization."""

    async def completion(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["tools"][0]["function"]["name"] == "query_entries"
        return {
            "id": "provider-request-tool",
            "object": "chat.completion",
            "created": 1,
            "model": "claude-sonnet",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "query_entries",
                                    "arguments": '{"query":"open"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }

    transport = LiteLLMSDKTransport(
        route=_route(),
        scope=_scope(),
        observer=_ignore_observation,
        completion=completion,
    )
    async with AsyncOpenAI(
        api_key="bridge-placeholder",
        base_url="http://litellm-sdk.invalid/v1",
        http_client=httpx.AsyncClient(transport=transport),
        max_retries=0,
    ) as client:
        result = await client.chat.completions.create(
            model="anthropic/claude-sonnet",
            messages=[{"role": "user", "content": "find open entries"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "query_entries",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        )

    tool_call = result.choices[0].message.tool_calls[0]
    assert tool_call.function.name == "query_entries"
    assert tool_call.function.arguments == '{"query":"open"}'


@pytest.mark.asyncio
async def test_bridge_streams_deltas_and_final_usage_chunk() -> None:
    """SSE translation preserves streamed content and its final usage chunk."""
    observations = []

    async def completion(**kwargs: Any) -> Any:
        assert kwargs["stream"] is True

        async def chunks():
            yield {
                "id": "provider-stream-1",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "claude-sonnet",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant"},
                        "finish_reason": None,
                    }
                ],
            }
            yield {
                "id": "provider-stream-1",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "claude-sonnet",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "hello"},
                        "finish_reason": None,
                    }
                ],
            }
            yield {
                "id": "provider-stream-1",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "claude-sonnet",
                "choices": [],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 1,
                    "total_tokens": 11,
                },
            }

        return chunks()

    async def observe(item: Any) -> None:
        observations.append(item)

    transport = LiteLLMSDKTransport(
        route=_route(), scope=_scope(), observer=observe, completion=completion
    )
    async with AsyncOpenAI(
        api_key="bridge-placeholder",
        base_url="http://litellm-sdk.invalid/v1",
        http_client=httpx.AsyncClient(transport=transport),
        max_retries=0,
    ) as client:
        stream = await client.chat.completions.create(
            model="anthropic/claude-sonnet",
            messages=[{"role": "user", "content": "hello"}],
            stream=True,
            stream_options={"include_usage": True},
        )
        deltas = [
            chunk.choices[0].delta.content
            async for chunk in stream
            if chunk.choices and chunk.choices[0].delta.content
        ]

    assert "hello" in deltas
    assert [item.outcome for item in observations] == [
        "dispatch_intent",
        "responded",
    ]
    assert observations[1].usage.input_tokens == 10
    assert observations[1].usage.output_tokens == 1
    assert observations[1].usage.complete is True


@pytest.mark.asyncio
async def test_stream_usage_merges_cost_from_wrapper_and_earlier_chunk() -> None:
    """Token counts and cost survive when LiteLLM exposes them separately."""
    observations = []

    class StreamResponse:
        _hidden_params = {"response_cost": 0.0042}

        def model_dump(self, **_kwargs: Any) -> dict[str, Any]:
            return {"id": "provider-stream-cost"}

        def __aiter__(self):
            async def chunks():
                yield {
                    "id": "provider-stream-cost",
                    "choices": [{"delta": {"content": "ok"}}],
                    "usage": {"cost": 0.0031},
                }
                yield {
                    "id": "provider-stream-cost",
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 20,
                        "completion_tokens": 2,
                        "total_tokens": 22,
                    },
                }

            return chunks()

    async def completion(**_kwargs: Any) -> Any:
        return StreamResponse()

    async def observe(item: Any) -> None:
        observations.append(item)

    transport = LiteLLMSDKTransport(
        route=_route(), scope=_scope(), observer=observe, completion=completion
    )
    async with AsyncOpenAI(
        api_key="bridge-placeholder",
        base_url="http://litellm-sdk.invalid/v1",
        http_client=httpx.AsyncClient(transport=transport),
        max_retries=0,
    ) as client:
        stream = await client.chat.completions.create(
            model="anthropic/claude-sonnet",
            messages=[{"role": "user", "content": "hello"}],
            stream=True,
            stream_options={"include_usage": True},
        )
        async for _chunk in stream:
            pass

    usage = observations[-1].usage
    assert usage.input_tokens == 20
    assert usage.output_tokens == 2
    assert usage.provider_cost_usd == Decimal("0.0031")
    assert usage.cost_source == "provider_response"
    assert usage.complete is True


@pytest.mark.asyncio
async def test_stream_usage_keeps_wrapper_cost_when_no_chunk_reports_cost() -> None:
    """The LiteLLM wrapper cost is retained alongside final chunk token usage."""
    observations = []

    class StreamResponse:
        _hidden_params = {"response_cost": 0.0042}

        def model_dump(self, **_kwargs: Any) -> dict[str, Any]:
            return {"id": "provider-stream-cost"}

        def __aiter__(self):
            async def chunks():
                yield {
                    "id": "provider-stream-cost",
                    "choices": [],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 2},
                }

            return chunks()

    async def completion(**_kwargs: Any) -> Any:
        return StreamResponse()

    async def observe(item: Any) -> None:
        observations.append(item)

    transport = LiteLLMSDKTransport(
        route=_route(), scope=_scope(), observer=observe, completion=completion
    )
    async with AsyncOpenAI(
        api_key="bridge-placeholder",
        base_url="http://litellm-sdk.invalid/v1",
        http_client=httpx.AsyncClient(transport=transport),
        max_retries=0,
    ) as client:
        stream = await client.chat.completions.create(
            model="anthropic/claude-sonnet",
            messages=[{"role": "user", "content": "hello"}],
            stream=True,
            stream_options={"include_usage": True},
        )
        async for _chunk in stream:
            pass

    usage = observations[-1].usage
    assert usage.input_tokens == 20
    assert usage.output_tokens == 2
    assert usage.provider_cost_usd == Decimal("0.0042")
    assert usage.cost_source == "litellm_response"
    assert usage.complete is True


@pytest.mark.asyncio
async def test_stream_close_during_terminal_observation_does_not_overwrite_outcome() -> (
    None
):
    """Concurrent HTTP cleanup cannot append unknown after a known response."""
    terminal_observation_started = asyncio.Event()
    allow_terminal_observation = asyncio.Event()
    outcomes = []

    async def chunks():
        yield {"choices": [{"delta": {"content": "done"}}]}

    async def finish(_last: Any | None, outcome: _ModelStreamOutcome) -> None:
        outcomes.append(outcome.value)
        if outcome is _ModelStreamOutcome.RESPONDED:
            terminal_observation_started.set()
            await allow_terminal_observation.wait()

    stream = _LiteLLMStream(chunks=chunks(), finish=finish)

    async def consume() -> None:
        async for _chunk in stream:
            pass

    consumer = asyncio.create_task(consume())
    await terminal_observation_started.wait()
    await stream.aclose()
    allow_terminal_observation.set()
    await consumer

    assert outcomes == ["responded"]


@pytest.mark.asyncio
async def test_duplicate_provider_tool_indexes_stay_as_separate_openai_calls() -> None:
    """Distinct call IDs must not merge when a provider reuses index zero."""
    stream_chunks = [
        {
            "id": "provider-stream-1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "claude-sonnet",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-scope",
                                "type": "function",
                                "function": {
                                    "name": "integral_get_scope",
                                    "arguments": "{}",
                                },
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "provider-stream-1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "claude-sonnet",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-tracks",
                                "type": "function",
                                "function": {
                                    "name": "integral_list_tracks",
                                    "arguments": "{}",
                                },
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "provider-stream-1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "claude-sonnet",
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "tool_calls",
                }
            ],
        },
    ]

    async def completion(**_kwargs: Any) -> Any:
        async def chunks():
            for chunk in stream_chunks:
                yield chunk

        return chunks()

    transport = LiteLLMSDKTransport(
        route=_route(),
        scope=_scope(),
        observer=_ignore_observation,
        completion=completion,
    )
    observed_calls = []
    async with AsyncOpenAI(
        api_key="bridge-placeholder",
        base_url="http://litellm-sdk.invalid/v1",
        http_client=httpx.AsyncClient(transport=transport),
        max_retries=0,
    ) as client:
        stream = await client.chat.completions.create(
            model="anthropic/claude-sonnet",
            messages=[{"role": "user", "content": "test"}],
            stream=True,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.tool_calls:
                observed_calls.extend(
                    (call.index, call.id, call.function.name)
                    for call in chunk.choices[0].delta.tool_calls
                )

    assert observed_calls == [
        (0, "call-scope", "integral_get_scope"),
        (1, "call-tracks", "integral_list_tracks"),
    ]


def test_idless_tool_prefix_is_adopted_when_call_id_arrives() -> None:
    """An initial anonymous prefix can merge with its later identified call."""
    from app.agentive.harness.litellm_model import _ToolCallIndexNormalizer

    normalizer = _ToolCallIndexNormalizer()
    prefix = normalizer.normalize(
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {}}]}}]}
    )
    identified = normalizer.normalize(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "id": "call-a", "function": {"name": "tool"}}
                        ]
                    }
                }
            ]
        }
    )

    assert prefix["choices"][0]["delta"]["tool_calls"][0]["index"] == 0
    assert identified["choices"][0]["delta"]["tool_calls"][0]["index"] == 0


@pytest.mark.asyncio
async def test_ambiguous_idless_delta_after_duplicate_provider_indexes_fails_closed() -> (
    None
):
    """An ID-less colliding delta cannot be safely assigned to a tool call."""
    import json

    from app.agentive.harness.litellm_model import AmbiguousToolCallStreamError

    async def chunks():
        yield {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "id": "call-a", "function": {"name": "a"}},
                            {"index": 0, "id": "call-b", "function": {"name": "b"}},
                        ]
                    },
                }
            ]
        }
        yield {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]
                    },
                }
            ]
        }

    async def finish(_last: Any | None, _outcome: _ModelStreamOutcome) -> None:
        return None

    stream = _LiteLLMStream(chunks=chunks(), finish=finish)
    iterator = stream.__aiter__()
    first = json.loads((await iterator.__anext__()).decode().removeprefix("data: "))
    assert [call["index"] for call in first["choices"][0]["delta"]["tool_calls"]] == [
        0,
        1,
    ]
    with pytest.raises(AmbiguousToolCallStreamError):
        await iterator.__anext__()


@pytest.mark.asyncio
async def test_closing_iterator_after_done_does_not_mark_response_unknown() -> None:
    """HTTP iterator cleanup after [DONE] must preserve its settled outcome."""
    outcomes = []

    async def chunks():
        yield {"choices": [{"delta": {"content": "done"}}]}

    async def finish(_last: Any | None, outcome: _ModelStreamOutcome) -> None:
        outcomes.append(outcome.value)

    stream = _LiteLLMStream(chunks=chunks(), finish=finish)
    iterator = stream.__aiter__()
    assert b"[DONE]" not in await iterator.__anext__()
    assert await iterator.__anext__() == b"data: [DONE]\n\n"
    await iterator.aclose()

    assert outcomes == ["responded"]


@pytest.mark.asyncio
async def test_locally_closed_provider_stream_records_cancelled() -> None:
    """User cancellation settles transport state without inventing usage."""
    outcomes = []
    usage_payloads = []

    async def chunks():
        yield {"choices": [{"delta": {"content": "partial"}}]}
        await asyncio.Event().wait()

    async def finish(last: Any | None, outcome: _ModelStreamOutcome) -> None:
        outcomes.append(outcome.value)
        usage_payloads.append(last.model_dump() if last is not None else {})

    stream = _LiteLLMStream(chunks=chunks(), finish=finish)
    iterator = stream.__aiter__()
    assert b"partial" in await iterator.__anext__()
    await iterator.aclose()

    assert outcomes == ["cancelled"]
    assert usage_payloads[0]["usage"] == {}


@pytest.mark.asyncio
async def test_bridge_rejects_model_route_mismatch_before_sdk_dispatch() -> None:
    """Request body cannot change the tenant-resolved model route."""
    dispatched = False

    async def completion(**kwargs: Any) -> dict[str, Any]:
        nonlocal dispatched
        dispatched = True
        return {}

    transport = LiteLLMSDKTransport(
        route=_route(), scope=_scope(), completion=completion
    )
    async with AsyncOpenAI(
        api_key="bridge-placeholder",
        base_url="http://litellm-sdk.invalid/v1",
        http_client=httpx.AsyncClient(transport=transport),
        max_retries=0,
    ) as client:
        with pytest.raises(BadRequestError):
            await client.chat.completions.create(
                model="openai/gpt-5",
                messages=[{"role": "user", "content": "wrong route"}],
            )

    assert dispatched is False


@pytest.mark.asyncio
async def test_bridge_fails_closed_when_dispatch_intent_cannot_be_recorded() -> None:
    """A provider call cannot begin if durable intent persistence fails."""
    dispatched = False

    async def completion(**kwargs: Any) -> dict[str, Any]:
        nonlocal dispatched
        dispatched = True
        return {}

    async def observer(item: Any) -> None:
        if item.outcome == "dispatch_intent":
            raise RuntimeError("durable storage unavailable")

    transport = LiteLLMSDKTransport(
        route=_route(),
        scope=_scope(),
        observer=observer,
        completion=completion,
    )
    async with AsyncOpenAI(
        api_key="bridge-placeholder",
        base_url="http://litellm-sdk.invalid/v1",
        http_client=httpx.AsyncClient(transport=transport),
        max_retries=0,
    ) as client:
        with pytest.raises(APIConnectionError):
            await client.chat.completions.create(
                model="anthropic/claude-sonnet",
                messages=[{"role": "user", "content": "do not dispatch"}],
            )

    assert dispatched is False


@pytest.mark.asyncio
async def test_interrupted_stream_records_one_unknown_outcome() -> None:
    """A truncated provider stream remains unsettled and is not double-appended."""
    observations = []

    async def completion(**kwargs: Any) -> Any:
        async def chunks():
            yield {
                "id": "provider-stream-uncertain",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "claude-sonnet",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "partial"},
                        "finish_reason": None,
                    }
                ],
            }
            raise RuntimeError("connection ended after partial response")

        return chunks()

    async def observer(item: Any) -> None:
        observations.append(item)

    transport = LiteLLMSDKTransport(
        route=_route(),
        scope=_scope(),
        observer=observer,
        completion=completion,
    )
    async with AsyncOpenAI(
        api_key="bridge-placeholder",
        base_url="http://litellm-sdk.invalid/v1",
        http_client=httpx.AsyncClient(transport=transport),
        max_retries=0,
    ) as client:
        stream = await client.chat.completions.create(
            model="anthropic/claude-sonnet",
            messages=[{"role": "user", "content": "continue"}],
            stream=True,
        )
        with pytest.raises(
            RuntimeError, match="connection ended after partial response"
        ):
            async for _ in stream:
                pass

    assert [item.outcome for item in observations] == [
        "dispatch_intent",
        "outcome_unknown",
    ]


def test_request_context_dimensions_are_content_free() -> None:
    from app.agentive.harness.litellm_model import _request_context

    body = {
        "messages": [
            {"role": "system", "content": "catalogue and loaded instructions"},
            {"role": "user", "content": "private user content"},
            {"role": "tool", "content": "private record content"},
        ],
        "tools": [{"type": "function", "function": {"name": "read_record"}}],
    }
    dimensions = _request_context(body)
    assert dimensions.message_count == 3
    assert dimensions.visible_tool_count == 1
    assert dimensions.instruction_chars > 0
    assert dimensions.conversation_chars > 0
    assert dimensions.tool_result_chars > 0
    assert dimensions.tool_schema_chars > 0
    assert "private" not in dimensions.model_dump_json()
    assert "read_record" not in dimensions.model_dump_json()
