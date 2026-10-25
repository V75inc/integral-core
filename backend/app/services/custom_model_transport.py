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
        timeout: float | httpx.Timeout,
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
        self._closed = False

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
        if self._closed:
            return
        self._closed = True
        try:
            close = getattr(self._stream, "aclose", None)
            if callable(close):
                await close()
        finally:
            if self._client is not None:
                await self._client.close()


class CustomModelSession:
    """Turn-owned connection reuse, with request-local credentials and guards.

    A session belongs to one immutable physical route. It is never shared
    across models, principals or turns. The guarded HTTP client still checks
    endpoint authority and DNS on every request, including reused connections.
    """

    def __init__(self, kwargs: dict[str, Any], *, keyless_endpoint: bool) -> None:
        from openai import AsyncOpenAI

        self._base = normalize_custom_base_url(kwargs["api_base"])
        self._key = kwargs["api_key"]
        self._keyless = keyless_endpoint
        self._closed = False
        self.client = AsyncOpenAI(
            api_key=self._key,
            base_url=self._base,
            http_client=CustomModelAsyncClient(
                self._base,
                timeout=float(kwargs["timeout"]),
                omit_authorization=keyless_endpoint,
            ),
            organization="",
            project="",
            max_retries=0,
        )

    def assert_route(self, kwargs: dict[str, Any], keyless_endpoint: bool) -> None:
        if (
            self._closed
            or normalize_custom_base_url(kwargs["api_base"]) != self._base
            or kwargs["api_key"] != self._key
            or keyless_endpoint != self._keyless
        ):
            raise ValueError("Custom model session route changed")

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            await self.client.close()


async def custom_endpoint_completion(
    completion: Any,
    kwargs: dict[str, Any],
    *,
    keyless_endpoint: bool = False,
    session: CustomModelSession | None = None,
) -> Any:
    """Use a guarded client, owned by this call or its explicit turn session."""
    if session is not None:
        session.assert_route(kwargs, keyless_endpoint)
        result = await completion(**kwargs, client=session.client)
        if kwargs.get("stream") and hasattr(result, "__aiter__"):
            # The stream closes its response; the turn owns the connection pool.
            return _OwnedModelStream(result, None)
        return result
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
