"""Meta WhatsApp Cloud API transport (Phase 9 Plan 09-03b, NOTIF-02 / A3).

Low-level HTTPX client that mirrors the ``email_service.send_email`` idiom:
async, never raises, returns a boolean-ish tuple so callers (the
``WhatsappChannel`` adapter) can surface a structured
``ChannelDispatchResult`` without caring how delivery happens.

A3 — first message uses a pre-approved welcome template to open Meta's
24-hour customer-service window. Subsequent messages within 24h after
each user-initiated reply may be freeform. The decision (template vs
freeform) lives in :class:`~app.services.notification_channels.whatsapp_channel.WhatsappChannel`,
which inspects ``Notification.metadata.dispatched_to[]`` for a prior
``{channel:"whatsapp", status:"sent"}`` entry — this module just sends
whatever ``kind`` the caller asked for.

Console-provider fallback (T-09-03b-V02 mitigation): when either
``WHATSAPP_CLOUD_API_TOKEN`` or ``WHATSAPP_PHONE_NUMBER_ID`` is empty,
this function returns ``(False, None)`` WITHOUT making an HTTP call —
no silent plaintext send to a default endpoint, no accidental network
egress in dev/CI environments that haven't configured Meta credentials.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def send_whatsapp_message(
    phone_e164: str,
    *,
    kind: str = "text",  # "text" | "template"
    text: Optional[str] = None,
    template_name: Optional[str] = None,
    template_lang: Optional[str] = None,
    template_params: Optional[List[dict]] = None,
) -> Tuple[bool, Optional[str]]:
    """Dispatch a WhatsApp message via the Meta Cloud API. Never raises.

    Args:
        phone_e164: E.164-formatted recipient phone (e.g. ``"+15551234567"``).
            Callers MUST validate the format before calling — this layer is
            transport-only. ``WhatsappChannel`` performs the regex check.
        kind: ``"template"`` for the first dispatch in a conversation
            (opens Meta's 24h window via a pre-approved template) or
            ``"text"`` for freeform messages within an open window.
        text: freeform body when ``kind="text"``. Empty string sent if None.
        template_name: template id when ``kind="template"``. Defaults to
            ``settings.WHATSAPP_WELCOME_TEMPLATE_NAME``.
        template_lang: BCP-47 language code for the template. Defaults to
            ``settings.WHATSAPP_WELCOME_TEMPLATE_LANG``.
        template_params: list of ``{"type":"text","text":"..."}`` dicts
            bound to the template's ``{{1}}``/``{{2}}``/... placeholders.

    Returns:
        ``(True, message_id)`` on HTTP 200 (Meta returns
        ``{"messages":[{"id": "<wamid>"}]}``); ``(False, None)`` on any
        non-200 response, network error, or missing-credentials path.
    """
    token = settings.WHATSAPP_CLOUD_API_TOKEN
    phone_id = settings.WHATSAPP_PHONE_NUMBER_ID
    if not token or not phone_id:
        logger.info(
            "whatsapp_service: WHATSAPP_CLOUD_API_TOKEN/WHATSAPP_PHONE_NUMBER_ID "
            "unset — falling back to console-provider no-op (no HTTP call)"
        )
        return False, None

    url = f"{settings.WHATSAPP_API_BASE}/{phone_id}/messages"
    if kind == "template":
        template_body: dict = {
            "name": template_name or settings.WHATSAPP_WELCOME_TEMPLATE_NAME,
            "language": {
                "code": template_lang or settings.WHATSAPP_WELCOME_TEMPLATE_LANG
            },
        }
        if template_params:
            template_body["components"] = [
                {"type": "body", "parameters": template_params}
            ]
        body: dict = {
            "messaging_product": "whatsapp",
            "to": phone_e164,
            "type": "template",
            "template": template_body,
        }
    else:
        body = {
            "messaging_product": "whatsapp",
            "to": phone_e164,
            "type": "text",
            "text": {"body": text or ""},
        }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
    except httpx.HTTPError as exc:
        logger.error("whatsapp_service: HTTPError to=%s exc=%r", phone_e164, exc)
        return False, None

    if resp.status_code == 200:
        try:
            data = resp.json() or {}
        except ValueError:
            logger.error(
                "whatsapp_service: 200 response with invalid JSON body to=%s",
                phone_e164,
            )
            return False, None
        messages = data.get("messages") or []
        ref = messages[0].get("id") if messages else None
        return True, ref

    logger.error(
        "whatsapp_service: send failed status=%s to=%s body=%s",
        resp.status_code,
        phone_e164,
        resp.text[:500],
    )
    return False, None
