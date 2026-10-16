"""Bounded, authority-bound transport for user-selected model endpoints."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.exceptions import BadRequestError
from app.services.model_provider_endpoints import normalize_custom_base_url
from app.services.url_safety import (
    DnsPinScope,
    outbound_trusted_hosts,
    validate_outbound_http_url,
)

MAX_MODEL_PROBE_BYTES = 65536
MODEL_PROBE_TIMEOUT_SECONDS = 10.0


class CustomModelAsyncClient(httpx.AsyncClient):
    """Validate and pin every SDK request; never redirect or use proxy env."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float,
        omit_authorization: bool = False,
        **kwargs: Any,
    ) -> None:
        self._endpoint = normalize_custom_base_url(base_url)
        self._pins = DnsPinScope()
        self._omit_authorization = omit_authorization
        super().__init__(
            timeout=timeout,
            trust_env=False,
            follow_redirects=False,
            event_hooks={"request": [self._guard_request]},
            **kwargs,
        )

    async def _guard_request(self, request: httpx.Request) -> None:
        if self._omit_authorization:
            request.headers.pop("authorization", None)
        base, target = urlsplit(self._endpoint), urlsplit(str(request.url))
        if (target.scheme, target.netloc) != (
            base.scheme,
            base.netloc,
        ) or not target.path.startswith(base.path + "/"):
            raise ValueError("Custom model request left its configured endpoint")
        if target.scheme != "https" and target.hostname not in outbound_trusted_hosts():
            raise ValueError(
                "Custom model endpoints require HTTPS unless the operator trusts the host"
            )
        await validate_outbound_http_url(str(request.url))
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, lambda: self._pins.pin_url(str(request.url), required=True)
        )

    async def aclose(self) -> None:
        try:
            await super().aclose()
        finally:
            self._pins.release()

    async def __aexit__(self, *args: Any) -> None:
        """Release pins even though httpx context exit bypasses aclose."""
        try:
            await super().__aexit__(*args)
        finally:
            self._pins.release()


async def probe_custom_model_endpoint(base_url: str, api_key: str) -> tuple[bool, str]:
    """Probe only the configured authority, with bounded body and total time."""
    try:
        base = normalize_custom_base_url(base_url)
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        headers["Accept-Encoding"] = "identity"
        async with (
            asyncio.timeout(MODEL_PROBE_TIMEOUT_SECONDS),
            CustomModelAsyncClient(base, timeout=MODEL_PROBE_TIMEOUT_SECONDS) as client,
        ):
            async with client.stream(
                "GET", f"{base}/models", headers=headers
            ) as response:
                if response.status_code in {401, 403}:
                    return False, (
                        "invalid API key" if api_key else "endpoint requires an API key"
                    )
                if response.status_code != 200:
                    return False, f"provider returned HTTP {response.status_code}"
                if response.headers.get(
                    "content-encoding", "identity"
                ).strip().lower() not in {"", "identity"}:
                    return False, "provider must return an uncompressed model list"
                body = bytearray()
                if response.is_stream_consumed:
                    # Already-buffered test transports; network responses take
                    # the bounded raw streaming path below.
                    if len(response.content) > MAX_MODEL_PROBE_BYTES:
                        return False, "provider model list is too large"
                    body.extend(response.content)
                else:
                    async for chunk in response.aiter_raw(chunk_size=8192):
                        if len(body) + len(chunk) > MAX_MODEL_PROBE_BYTES:
                            return False, "provider model list is too large"
                        body.extend(chunk)
        payload = json.loads(body)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            return False, "endpoint did not return an OpenAI-compatible model list"
        return True, "validated" if api_key else "validated (no API key)"
    except (
        httpx.HTTPError,
        httpx.InvalidURL,
        BadRequestError,
        ValueError,
        TimeoutError,
    ):
        # Arbitrary endpoint bodies and URL values must never enter logs/errors.
        return False, "could not safely reach the custom model endpoint"


class _OwnedModelStream:
    """Keep the guarded SDK client alive until stream completion or close."""

    def __init__(self, stream: Any, client: Any) -> None:
        self._stream, self._iterator, self._client = stream, stream.__aiter__(), client

    def __aiter__(self) -> "_OwnedModelStream":
        return self

    @property
    def _hidden_params(self) -> dict[str, Any]:
        """Preserve LiteLLM cost/request-header metadata for Core accounting."""
        value = getattr(self._stream, "_hidden_params", None)
        return value if isinstance(value, dict) else {}

    async def __anext__(self) -> Any:
        try:
            return await anext(self._iterator)
        except BaseException:
            await self.aclose()
            raise

    async def aclose(self) -> None:
        try:
            close = getattr(self._stream, "aclose", None)
            if callable(close):
                await close()
        finally:
            await self._client.close()


async def custom_endpoint_completion(
    completion: Any,
    kwargs: dict[str, Any],
    *,
    keyless_endpoint: bool = False,
) -> Any:
    """Give LiteLLM an explicit guarded OpenAI SDK client for this call only."""
    from openai import AsyncOpenAI

    base = normalize_custom_base_url(kwargs["api_base"])
    http_client = CustomModelAsyncClient(
        base,
        timeout=float(kwargs["timeout"]),
        omit_authorization=keyless_endpoint,
    )
    client = AsyncOpenAI(
        api_key=kwargs["api_key"],
        base_url=base,
        http_client=http_client,
        organization="",
        project="",
        max_retries=0,
    )
    try:
        result = await completion(**kwargs, client=client)
        if kwargs.get("stream") and hasattr(result, "__aiter__"):
            return _OwnedModelStream(result, client)
    except BaseException:
        await client.close()
        raise
    await client.close()
    return result
