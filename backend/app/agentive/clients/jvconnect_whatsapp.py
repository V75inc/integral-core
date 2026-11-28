"""Thin httpx client for a customer-supplied jvconnect WhatsApp API."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.exceptions import BadRequestError
from app.services.url_safety import pin_public_dns, validate_outbound_http_url

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10.0
_API_PREFIX = "/api/v1/meta/whatsapp"


@dataclass(frozen=True)
class JvconnectResult:
    """Structured outcome of one jvconnect HTTP call."""

    ok: bool
    status_code: int
    data: dict[str, Any]
    error: str
    retry_after_seconds: Optional[int] = None


def _endpoint(base_url: str, suffix: str) -> str:
    return f"{base_url.rstrip('/')}/{_API_PREFIX.strip('/')}/{suffix}"


def _bearer_headers(api_key: str) -> dict[str, str]:
    # Built for the request only. Callers must not log this mapping.
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }


def _transport_error(exc: httpx.RequestError) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "request timed out"
    if isinstance(exc, httpx.ConnectError):
        return "connection failed"
    return "request failed"


def _coerce_retry_after(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _retry_after_seconds(
    response: httpx.Response, data: dict[str, Any]
) -> Optional[int]:
    if response.status_code != 429:
        return None
    from_header = _coerce_retry_after(response.headers.get("Retry-After"))
    if from_header is not None:
        return from_header
    return _coerce_retry_after(data.get("retry_after"))


def _error_text(data: dict[str, Any], status_code: int) -> str:
    for key in ("error", "message"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"HTTP {status_code}"


def _parse_data(response: httpx.Response) -> Optional[dict[str, Any]]:
    raw = response.content
    if not raw or not raw.strip():
        return {}
    try:
        parsed = response.json()
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _result_from_response(response: httpx.Response) -> JvconnectResult:
    status_code = response.status_code
    data = _parse_data(response)
    if data is None:
        return JvconnectResult(
            ok=False,
            status_code=status_code,
            data={},
            error="invalid JSON response",
            retry_after_seconds=_retry_after_seconds(response, {}),
        )
    if 200 <= status_code < 300:
        return JvconnectResult(
            ok=True,
            status_code=status_code,
            data=data,
            error="",
            retry_after_seconds=None,
        )
    return JvconnectResult(
        ok=False,
        status_code=status_code,
        data=data,
        error=_error_text(data, status_code),
        retry_after_seconds=_retry_after_seconds(response, data),
    )


def _log_result(operation: str, result: JvconnectResult) -> None:
    # Status only. Never log the bearer token or a response body (webhook secret).
    if result.ok:
        logger.info("jvconnect whatsapp %s succeeded", operation)
        return
    logger.warning(
        "jvconnect whatsapp %s failed status=%s",
        operation,
        result.status_code,
    )


async def _perform(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    api_key: str,
    json_body: Optional[dict[str, Any]],
) -> JvconnectResult:
    try:
        response = await client.request(
            method,
            url,
            headers=_bearer_headers(api_key),
            json=json_body,
        )
    except httpx.RequestError as exc:
        return JvconnectResult(
            ok=False,
            status_code=0,
            data={},
            error=_transport_error(exc),
            retry_after_seconds=None,
        )
    return _result_from_response(response)


async def _call(
    method: str,
    url: str,
    api_key: str,
    *,
    json_body: Optional[dict[str, Any]] = None,
    client: Optional[httpx.AsyncClient] = None,
    operation: str,
) -> JvconnectResult:
    if client is None:
        # The base URL is customer-supplied and the request carries their key:
        # refuse private/link-local targets and pin DNS against rebinding.
        try:
            await validate_outbound_http_url(url)
            with pin_public_dns(url):
                async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as owned:
                    result = await _perform(owned, method, url, api_key, json_body)
        except BadRequestError:
            result = JvconnectResult(
                ok=False, status_code=0, data={}, error="host not allowed"
            )
    else:
        result = await _perform(client, method, url, api_key, json_body)
    _log_result(operation, result)
    return result


async def fetch_account(
    base_url: str,
    api_key: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> JvconnectResult:
    """GET the WhatsApp account bound to this jvconnect API key."""
    return await _call(
        "GET",
        _endpoint(base_url, "account"),
        api_key,
        client=client,
        operation="account",
    )


async def fetch_calling_settings(
    base_url: str,
    api_key: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> JvconnectResult:
    """GET Meta calling ENABLED/DISABLED for the key-bound phone."""
    return await _call(
        "GET",
        _endpoint(base_url, "calling/settings"),
        api_key,
        client=client,
        operation="calling settings",
    )


async def set_calling_settings(
    base_url: str,
    api_key: str,
    enabled: bool,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> JvconnectResult:
    """POST Meta calling on or off for the key-bound phone."""
    return await _call(
        "POST",
        _endpoint(base_url, "calling/settings"),
        api_key,
        json_body={"enabled": enabled},
        client=client,
        operation="calling settings update",
    )


async def fetch_calling_credentials(
    base_url: str,
    api_key: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> JvconnectResult:
    """GET the Meta token jvvoice needs for AcceptWhatsAppCall.

    The access_token stays in the result data for the caller to forward.
    This function does not log or store it.
    """
    return await _call(
        "GET",
        _endpoint(base_url, "calling/credentials"),
        api_key,
        client=client,
        operation="calling credentials",
    )


async def register_webhook(
    base_url: str,
    api_key: str,
    callback_url: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> JvconnectResult:
    """POST the callback URL and return jvconnect's webhook registration."""
    return await _call(
        "POST",
        _endpoint(base_url, "webhook/register"),
        api_key,
        json_body={"callback_url": callback_url},
        client=client,
        operation="webhook register",
    )


async def send_typing_indicator(
    base_url: str,
    api_key: str,
    message_id: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> JvconnectResult:
    """Show the WhatsApp typing indicator for one inbound message.

    Meta shows it after a read receipt on that message id, for about 25
    seconds or until the next outbound message.
    """
    return await _call(
        "POST",
        _endpoint(base_url, "messages"),
        api_key,
        json_body={
            "message": {
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": message_id,
                "typing_indicator": {"type": "text"},
            }
        },
        client=client,
        operation="typing indicator",
    )


async def send_text(
    base_url: str,
    api_key: str,
    to: str,
    body: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> JvconnectResult:
    """POST a WhatsApp text message through jvconnect."""
    return await _call(
        "POST",
        _endpoint(base_url, "messages"),
        api_key,
        json_body={
            "message": {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"body": body},
            }
        },
        client=client,
        operation="send text",
    )
