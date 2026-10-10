"""Content-free failures marked only at the outbound model SDK boundary."""

from __future__ import annotations

import sys

import httpx


class NativeModelRequestError(Exception):
    """A known provider failure, distinct from Core persistence/authority errors."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def model_request_error(exc: BaseException) -> NativeModelRequestError | None:
    """Classify typed SDK failures without reading private exception text."""
    # The actual LiteLLM dispatch already imports the SDK. Do not import it
    # merely to report a host error: its development import can load .env.
    sdk = sys.modules.get("litellm")
    categories = (
        ("ContextWindowExceededError", "model_context_limit"),
        ("AuthenticationError", "model_authentication_failed"),
        ("PermissionDeniedError", "model_authentication_failed"),
        ("RateLimitError", "model_rate_limited"),
        ("NotFoundError", "model_unavailable"),
        ("Timeout", "model_request_timeout"),
        ("APIConnectionError", "model_endpoint_unreachable"),
        ("ServiceUnavailableError", "model_provider_unavailable"),
        ("BadRequestError", "model_request_rejected"),
    )
    for name, code in categories:
        error_type = getattr(sdk, name, None)
        if isinstance(error_type, type) and isinstance(exc, error_type):
            return NativeModelRequestError(code)
    if isinstance(exc, httpx.TimeoutException):
        return NativeModelRequestError("model_request_timeout")
    if isinstance(exc, httpx.NetworkError):
        return NativeModelRequestError("model_endpoint_unreachable")
    return None


def classified_model_error(exc: BaseException) -> str | None:
    """Follow client wrappers only to our own SDK-boundary marker."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, NativeModelRequestError):
            return current.code
        current = current.__cause__ or current.__context__
    return None
