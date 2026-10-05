"""Pydantic AI model transport that keeps requests on Integral's LiteLLM SDK path.

Pydantic AI's LiteLLM provider is backed by an OpenAI-compatible HTTP client.
Integral does not currently configure a LiteLLM Proxy, so this transport
adapts that public client boundary to LiteLLM's in-process async SDK. No request
is sent over the network. A fresh transport is created for each trusted model
route and execution scope.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx

from app.agentive.harness.contracts import (
    HarnessExecutionScope,
    ModelUsageObservation,
    PhysicalModelRequest,
    ResolvedModelRoute,
)
from app.agentive.harness.pydantic_ai_compat import LiteLLMProvider, OpenAIChatModel

logger = logging.getLogger(__name__)

AttemptObserver = Callable[[PhysicalModelRequest], Awaitable[None]]


class AmbiguousToolCallStreamError(ValueError):
    """A provider reused tool indexes without enough identity to disambiguate."""


class _ToolCallIndexNormalizer:
    """Repair provider index collisions before OpenAI's stream parser sees them.

    Some OpenAI-compatible adapters (including the configured Ollama path) emit
    multiple tool calls with the same provider index. Pydantic AI uses that
    index as the identity of a streamed part, so the bridge must provide stable
    unique indexes keyed by the provider's tool-call ID.
    """

    def __init__(self) -> None:
        self._call_indexes: dict[str, int] = {}
        self._provider_index_calls: dict[int, set[str]] = defaultdict(set)
        self._anonymous_indexes: dict[int, int] = {}
        self._used_indexes: set[int] = set()
        self._next_index = 0

    def _allocate(self, preferred: int | None = None) -> int:
        if preferred is not None and preferred not in self._used_indexes:
            index = preferred
        else:
            while self._next_index in self._used_indexes:
                self._next_index += 1
            index = self._next_index
            self._next_index += 1
        self._used_indexes.add(index)
        return index

    def normalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return a JSON-safe copy with collision-free tool-call indexes."""
        choices = payload.get("choices")
        if not isinstance(choices, list):
            return payload

        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue
            calls = delta.get("tool_calls")
            if not isinstance(calls, list):
                continue

            for call in calls:
                if not isinstance(call, dict):
                    continue
                raw_index = call.get("index")
                provider_index = (
                    raw_index if type(raw_index) is int and raw_index >= 0 else None
                )
                call_id = call.get("id")
                if isinstance(call_id, str) and call_id:
                    if call_id not in self._call_indexes:
                        known_calls = self._provider_index_calls.get(
                            provider_index, set()
                        )
                        anonymous_index = (
                            self._anonymous_indexes.pop(provider_index, None)
                            if provider_index is not None and not known_calls
                            else None
                        )
                        if anonymous_index is None:
                            if known_calls:
                                logger.warning(
                                    "provider reused streamed tool-call index; "
                                    "normalizing distinct calls"
                                )
                            anonymous_index = self._allocate(provider_index)
                        self._call_indexes[call_id] = anonymous_index
                    normalized_index = self._call_indexes[call_id]
                    if provider_index is not None:
                        self._provider_index_calls[provider_index].add(call_id)
                elif provider_index is not None:
                    known_calls = self._provider_index_calls.get(provider_index, set())
                    if len(known_calls) > 1:
                        raise AmbiguousToolCallStreamError(
                            "provider reused a tool-call index and omitted call identity"
                        )
                    if len(known_calls) == 1:
                        only_call_id = next(iter(known_calls))
                        normalized_index = self._call_indexes[only_call_id]
                    else:
                        normalized_index = self._anonymous_indexes.get(provider_index)
                        if normalized_index is None:
                            normalized_index = self._allocate(provider_index)
                            self._anonymous_indexes[provider_index] = normalized_index
                else:
                    raise AmbiguousToolCallStreamError(
                        "provider omitted both tool-call index and call identity"
                    )
                call["index"] = normalized_index
        return payload


def _plain(value: Any) -> Any:
    """Convert SDK/Pydantic responses to JSON-compatible values."""
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return value
    raise TypeError("LiteLLM returned an unsupported response object")


def _cost_value(value: Any) -> Decimal | None:
    """Return a finite, non-negative USD amount without coercing bad data."""
    if value is None:
        return None
    try:
        cost = Decimal(str(value))
    except (TypeError, ValueError):
        return None
    return cost if cost.is_finite() and cost >= 0 else None


def _reported_cost(
    response: Any, payload: dict[str, Any]
) -> tuple[Decimal | None, str]:
    """Read explicit provider or LiteLLM cost fields before estimating."""
    hidden = getattr(response, "_hidden_params", None)
    hidden = hidden if isinstance(hidden, dict) else {}
    raw_usage = payload.get("usage")
    raw_usage = raw_usage if isinstance(raw_usage, dict) else {}
    usage_cost = raw_usage.get("cost", raw_usage.get("response_cost"))
    provider_cost = _cost_value(usage_cost)
    if provider_cost is not None:
        return provider_cost, "provider_response"

    for key in ("response_cost", "cost"):
        direct_cost = _cost_value(payload.get(key))
        if direct_cost is not None:
            return direct_cost, "provider_response"
    headers = hidden.get("additional_headers")
    if isinstance(headers, dict):
        header_cost = _cost_value(headers.get("llm_provider-x-litellm-response-cost"))
        if header_cost is not None:
            return header_cost, "provider_response"
    hidden_cost = _cost_value(hidden.get("response_cost"))
    if hidden_cost is not None:
        return hidden_cost, "litellm_response"
    return None, "unavailable"


def _calculate_litellm_cost(
    response: Any,
    *,
    model: str | None,
    provider: str | None,
) -> Decimal | None:
    """Calculate cost only when LiteLLM has a known price for this route."""
    if not model:
        return None
    try:
        import litellm

        model_info = litellm.get_model_info(model=model, custom_llm_provider=provider)
        rates = (
            (
                _cost_value(model_info.get(rate))
                for rate in ("input_cost_per_token", "output_cost_per_token")
            )
            if isinstance(model_info, dict)
            else ()
        )
        # LiteLLM uses 0.0 for some unknown/local routes. Without explicit
        # provider cost, treating that default as a price would silently
        # under-report metered cloud usage (for example Ollama cloud models).
        if not any(rate is not None and rate > 0 for rate in rates):
            return None
        amount = litellm.completion_cost(
            completion_response=response,
            model=model,
            custom_llm_provider=provider,
            call_type="acompletion",
        )
    except Exception:
        # Missing or incompatible pricing is a visible unavailable value; it
        # must not discard a completed response or be represented as $0.00.
        logger.debug(
            "LiteLLM cost calculation unavailable for model route %s/%s",
            provider,
            model,
            exc_info=True,
        )
        return None
    return _cost_value(amount)


def _usage(
    response: Any,
    *,
    complete: bool,
    model: str | None = None,
    provider: str | None = None,
) -> ModelUsageObservation:
    """Extract reported usage, then use LiteLLM pricing when cost is omitted."""
    payload = _plain(response)
    raw_usage = payload.get("usage") if isinstance(payload, dict) else None
    raw_usage = raw_usage if isinstance(raw_usage, dict) else {}
    prompt_details = raw_usage.get("prompt_tokens_details") or {}
    completion_details = raw_usage.get("completion_tokens_details") or {}
    normalized_cost, cost_source = _reported_cost(response, payload)
    if (
        normalized_cost is None
        and raw_usage.get("prompt_tokens") is not None
        and raw_usage.get("completion_tokens") is not None
    ):
        normalized_cost = _calculate_litellm_cost(
            response, model=model, provider=provider
        )
        if normalized_cost is not None:
            cost_source = "litellm_calculated"
    return ModelUsageObservation(
        input_tokens=raw_usage.get("prompt_tokens"),
        output_tokens=raw_usage.get("completion_tokens"),
        cached_input_tokens=prompt_details.get("cached_tokens"),
        reasoning_tokens=completion_details.get("reasoning_tokens"),
        provider_cost_usd=normalized_cost,
        cost_source=cost_source,
        complete=complete
        and raw_usage.get("prompt_tokens") is not None
        and raw_usage.get("completion_tokens") is not None,
    )


def _provider_request_id(response: Any) -> str | None:
    """Return a provider-generated ID when the SDK response exposes one."""
    value = getattr(response, "id", None)
    if value is None and isinstance(response, dict):
        value = response.get("id")
    return str(value) if value else None


class _StreamAccountingResponse:
    """Small merged response used only for a stream's usage observation."""

    def __init__(
        self,
        *,
        payload: dict[str, Any],
        hidden_params: dict[str, Any],
    ) -> None:
        self._payload = payload
        self._hidden_params = hidden_params
        self.id = payload.get("id")

    def model_dump(self, **_kwargs: Any) -> dict[str, Any]:
        return self._payload


class _LiteLLMStream(httpx.AsyncByteStream):
    """Render LiteLLM stream chunks as the SSE shape expected by OpenAI SDK."""

    def __init__(
        self,
        *,
        chunks: AsyncIterator[Any],
        finish: Callable[[Any | None, bool], Awaitable[None]],
        response: Any = None,
    ) -> None:
        self._chunks = chunks
        self._finish = finish
        self._last: Any | None = None
        self._usage: dict[str, Any] = {}
        self._reported_usage_cost: Any = None
        self._reported_direct_cost: Any = None
        self._hidden_params: dict[str, Any] = {}
        self._hidden_cost: Any = None
        self._additional_headers: dict[str, Any] = {}
        self._provider_request_id: Any = None
        self._capture(response)
        self._finished = False
        self._tool_call_indexes = _ToolCallIndexNormalizer()

    def _capture(self, response: Any) -> None:
        """Retain accounting metadata without retaining every streamed token."""
        try:
            payload = _plain(response)
        except (TypeError, ValueError):
            payload = {}
        if isinstance(payload, dict):
            for key in ("cost", "response_cost"):
                if _cost_value(payload.get(key)) is not None:
                    self._reported_direct_cost = payload[key]
            usage = payload.get("usage")
            if isinstance(usage, dict):
                self._usage.update(usage)
                for key in ("cost", "response_cost"):
                    if _cost_value(usage.get(key)) is not None:
                        self._reported_usage_cost = usage[key]
            request_id = payload.get("id")
            if request_id:
                self._provider_request_id = request_id

        hidden = getattr(response, "_hidden_params", None)
        if isinstance(hidden, dict):
            self._hidden_params.update(hidden)
            hidden_cost = _cost_value(hidden.get("response_cost"))
            # Prefer a later non-zero hidden cost if LiteLLM first exposes its
            # streaming placeholder (0.0) and then the settled amount.
            if hidden_cost is not None and (
                self._hidden_cost is None or hidden_cost > 0
            ):
                self._hidden_cost = hidden["response_cost"]
            headers = hidden.get("additional_headers")
            if isinstance(headers, dict):
                self._additional_headers.update(headers)

    def _accounting_response(self) -> Any:
        """Expose merged stream metadata in the shape consumed by ``_usage``."""
        payload: dict[str, Any] = {}
        if self._last is not None:
            try:
                last_payload = _plain(self._last)
            except (TypeError, ValueError):
                last_payload = {}
            if isinstance(last_payload, dict):
                payload.update(last_payload)
        payload["id"] = self._provider_request_id or payload.get("id")
        merged_usage = dict(self._usage)
        if self._reported_usage_cost is not None:
            merged_usage["cost"] = self._reported_usage_cost
        payload["usage"] = merged_usage
        if self._reported_direct_cost is not None:
            payload["response_cost"] = self._reported_direct_cost

        hidden = dict(self._hidden_params)
        if self._hidden_cost is not None:
            hidden["response_cost"] = self._hidden_cost
        if self._additional_headers:
            hidden["additional_headers"] = self._additional_headers
        return _StreamAccountingResponse(payload=payload, hidden_params=hidden)

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._chunks:
                self._last = chunk
                self._capture(chunk)
                payload = _plain(chunk)
                if isinstance(payload, dict):
                    payload = self._tool_call_indexes.normalize(deepcopy(payload))
                data = json.dumps(payload, separators=(",", ":"))
                yield f"data: {data}\n\n".encode("utf-8")
            # Claim settlement before awaiting persistence. The HTTP client may
            # close the response concurrently while the terminal observation is
            # being written; allowing aclose() to win that race would append an
            # outcome_unknown after the known response and permanently block
            # safe session continuation.
            self._finished = True
            await self._finish(self._accounting_response(), True)
            yield b"data: [DONE]\n\n"
        except BaseException:
            if not self._finished:
                self._finished = True
                await self._finish(self._accounting_response(), False)
            raise

    async def aclose(self) -> None:
        close = getattr(self._chunks, "aclose", None)
        if callable(close):
            await close()
        if not self._finished:
            self._finished = True
            await self._finish(self._accounting_response(), False)


class LiteLLMSDKTransport(httpx.AsyncBaseTransport):
    """Translate one OpenAI-compatible client request into LiteLLM SDK work."""

    def __init__(
        self,
        *,
        route: ResolvedModelRoute,
        scope: HarnessExecutionScope,
        observer: AttemptObserver | None = None,
        completion: Callable[..., Awaitable[Any]] | None = None,
        timeout_seconds: float = 180.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("model request timeout must be positive")
        self._route = route
        self._scope = scope
        self._observer = observer
        self._completion = completion
        self._timeout_seconds = timeout_seconds

    async def _sdk_completion(self, **kwargs: Any) -> Any:
        completion = self._completion
        if completion is None:
            import litellm

            completion = litellm.acompletion
        return await completion(**kwargs)

    async def _observe(
        self,
        *,
        request_id: str,
        started_at: datetime,
        outcome: str,
        response: Any | None = None,
        complete: bool = False,
        required: bool = False,
    ) -> None:
        if self._observer is None:
            if required:
                raise RuntimeError(
                    "native model dispatch requires a durable attempt observer"
                )
            return
        usage = (
            _usage(
                response,
                complete=complete,
                model=self._route.model,
                provider=self._route.provider,
            )
            if response is not None
            else None
        )
        observation = PhysicalModelRequest(
            request_id=request_id,
            scope=self._scope,
            provider=self._route.provider,
            model=self._route.model,
            attempt=1,
            dispatched_at=started_at,
            observed_at=datetime.now(timezone.utc),
            outcome=outcome,
            provider_request_id=(
                _provider_request_id(response) if response is not None else None
            ),
            usage=usage,
        )
        try:
            await self._observer(observation)
        except Exception:
            if required:
                raise
            # Accounting persistence failure must be visible operationally;
            # it must not turn a completed provider response into a lost turn.
            logger.exception("native model attempt observation failed")

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "POST" or not request.url.path.endswith(
            "/chat/completions"
        ):
            return httpx.Response(
                404,
                json={"error": {"message": "Unsupported LiteLLM SDK bridge route"}},
                request=request,
            )

        try:
            body = json.loads(await request.aread())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return httpx.Response(
                400,
                json={"error": {"message": "Malformed model request"}},
                request=request,
            )
        if not isinstance(body, dict) or body.get("model") != self._route.model:
            return httpx.Response(
                400,
                json={
                    "error": {"message": "Model route does not match execution scope"}
                },
                request=request,
            )

        request_id = str(uuid4())
        started_at = datetime.now(timezone.utc)
        kwargs = dict(body)
        kwargs["num_retries"] = 0  # Core owns retry identity and accounting.
        # LiteLLM has provider-specific default timeouts, which can leave a
        # broken streaming response waiting far beyond Integral's intended
        # chat latency. Bound each physical request while preserving Core's
        # longer run budget for legitimate multi-step work.
        kwargs["timeout"] = self._timeout_seconds
        if self._route.ollama_num_ctx is not None:
            # LiteLLM's `ollama_chat` mapper does not translate `num_ctx` from
            # arbitrary kwargs. Pass it through OpenAI's extra_body so LiteLLM
            # merges Ollama's native `{options: {num_ctx: ...}}` into the final
            # `/api/chat` payload. A top-level `num_ctx` silently disappears.
            extra_body = kwargs.get("extra_body")
            extra_body = dict(extra_body) if isinstance(extra_body, dict) else {}
            ollama_options = extra_body.get("options")
            ollama_options = (
                dict(ollama_options) if isinstance(ollama_options, dict) else {}
            )
            ollama_options["num_ctx"] = self._route.ollama_num_ctx
            extra_body["options"] = ollama_options
            kwargs["extra_body"] = extra_body
        if self._route.ollama_num_predict is not None:
            # Pydantic AI may encode its max_tokens setting as
            # `max_completion_tokens` for this OpenAI-compatible model profile.
            # LiteLLM's Ollama adapter accepts both spellings; passing both can
            # let the smaller generic value win. Collapse the aliases to the
            # route-owned max_tokens, which LiteLLM maps to num_predict.
            kwargs.pop("max_completion_tokens", None)
            kwargs["max_tokens"] = self._route.ollama_num_predict
        if (
            self._route.ollama_think is not None
            or self._route.ollama_clear_thinking is not None
        ):
            # Thinking options are model-defined in Ollama's API. Keep them
            # inside this route adapter, and omit them unless the deployment
            # explicitly configures a value supported by its selected model.
            extra_body = kwargs.get("extra_body")
            extra_body = dict(extra_body) if isinstance(extra_body, dict) else {}
            if self._route.ollama_think is not None:
                extra_body["think"] = self._route.ollama_think
            if self._route.ollama_clear_thinking is not None:
                extra_body["clear_thinking"] = self._route.ollama_clear_thinking
            kwargs["extra_body"] = extra_body
        if self._route.api_base is not None:
            kwargs["api_base"] = self._route.api_base
        if self._route.api_key is not None:
            kwargs["api_key"] = self._route.api_key.get_secret_value()
        else:
            kwargs.pop("api_key", None)

        await self._observe(
            request_id=request_id,
            started_at=started_at,
            outcome="dispatch_intent",
            required=True,
        )

        try:
            response = await self._sdk_completion(**kwargs)
        except BaseException:
            await self._observe(
                request_id=request_id,
                started_at=started_at,
                outcome="outcome_unknown",
            )
            raise

        if body.get("stream") is True:
            if not hasattr(response, "__aiter__"):
                await self._observe(
                    request_id=request_id,
                    started_at=started_at,
                    outcome="failed",
                )
                return httpx.Response(
                    502,
                    json={"error": {"message": "LiteLLM stream was not asynchronous"}},
                    request=request,
                )

            async def finish(last: Any | None, complete: bool) -> None:
                await self._observe(
                    request_id=request_id,
                    started_at=started_at,
                    outcome="responded" if complete else "outcome_unknown",
                    response=last,
                    complete=complete,
                )

            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=_LiteLLMStream(
                    chunks=response, response=response, finish=finish
                ),
                request=request,
            )

        try:
            payload = _plain(response)
            encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        except Exception:
            await self._observe(
                request_id=request_id,
                started_at=started_at,
                outcome="failed",
            )
            return httpx.Response(
                502,
                json={"error": {"message": "LiteLLM returned an unsupported response"}},
                request=request,
            )

        await self._observe(
            request_id=request_id,
            started_at=started_at,
            outcome="responded",
            response=response,
            complete=True,
        )
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=encoded,
            request=request,
        )


def build_litellm_sdk_model(
    *,
    route: ResolvedModelRoute,
    scope: HarnessExecutionScope,
    observer: AttemptObserver,
    completion: Callable[..., Awaitable[Any]] | None = None,
    timeout_seconds: float = 180.0,
) -> OpenAIChatModel:
    """Build an OpenAI-compatible Pydantic model over LiteLLM's in-process SDK."""
    transport = LiteLLMSDKTransport(
        route=route,
        scope=scope,
        observer=observer,
        completion=completion,
        timeout_seconds=timeout_seconds,
    )
    http_client = httpx.AsyncClient(
        transport=transport, timeout=httpx.Timeout(timeout_seconds)
    )
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key="integral-litellm-sdk-bridge",
        base_url="http://litellm-sdk.invalid/v1",
        http_client=http_client,
        max_retries=0,
    )
    provider = LiteLLMProvider(openai_client=client)
    return OpenAIChatModel(route.model, provider=provider)
