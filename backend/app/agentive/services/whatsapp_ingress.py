"""HMAC verification and Meta envelope parsing for inbound WhatsApp."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from typing import Literal

from app.agentive.services.whatsapp_channel_binding import (
    read_api_key,
    read_webhook_secret,
)
from app.api.errors import MissingAuthenticationError

logger = logging.getLogger(__name__)

_TEXT_ONLY_NOTICE = "Text only for now"
_LINK_NOTICE = "Link your number in Integral settings"


@dataclass(frozen=True)
class ParsedWhatsAppEnvelope:
    """First inbound message taken from a Meta WhatsApp webhook envelope."""

    kind: Literal["text", "non_text", "ignore"]
    phone_number_id: str
    sender_id: str
    message_id: str
    text: str


def verify_jvconnect_signature(raw_body: bytes, secret: str, signature: str) -> bool:
    """Return whether ``signature`` is the jvconnect HMAC-SHA256 of ``raw_body``.

    jvconnect prefixes the lowercase hex digest with ``sha256=``. Comparison
    uses ``hmac.compare_digest``. Missing, forged, truncated, wrong-length,
    and empty-secret inputs return False.
    """
    if not isinstance(raw_body, (bytes, bytearray)) or not isinstance(secret, str):
        return False
    if secret == "" or not isinstance(signature, str) or signature == "":
        return False
    try:
        digest = hmac.new(
            secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
    except (TypeError, ValueError):
        return False
    expected = "sha256=" + digest
    try:
        return hmac.compare_digest(signature.encode("utf-8"), expected.encode("ascii"))
    except (TypeError, ValueError):
        return False


def parse_whatsapp_envelope(payload: dict) -> ParsedWhatsAppEnvelope:
    """Return the first WhatsApp message in a Meta webhook envelope.

    A text message with a string body is ``text``. Any other message type,
    including text with no body, is ``non_text``. Status-only payloads, an
    empty messages array, missing entry or changes, and a non-dict payload
    are ``ignore``. Only the first entry, change, and message are read.
    """
    if not isinstance(payload, dict):
        return _ignored()
    entry = _first_mapping(payload.get("entry"))
    if entry is None:
        return _ignored()
    change = _first_mapping(entry.get("changes"))
    if change is None:
        return _ignored()
    value = change.get("value")
    if not isinstance(value, dict):
        return _ignored()
    message = _first_mapping(value.get("messages"))
    if message is None:
        return _ignored()
    return _from_message(value, message)


def _ignored() -> ParsedWhatsAppEnvelope:
    return ParsedWhatsAppEnvelope(
        kind="ignore",
        phone_number_id="",
        sender_id="",
        message_id="",
        text="",
    )


def _first_mapping(value: object) -> dict | None:
    if not isinstance(value, list) or not value:
        return None
    first = value[0]
    if not isinstance(first, dict):
        return None
    return first


def _from_message(value: dict, message: dict) -> ParsedWhatsAppEnvelope:
    metadata = value.get("metadata")
    phone_number_id = ""
    if isinstance(metadata, dict):
        phone_number_id = _string(metadata.get("phone_number_id"))
    sender_id = _string(message.get("from"))
    message_id = _string(message.get("id"))
    if message.get("type") == "text":
        body = _text_body(message.get("text"))
        if body is not None:
            return ParsedWhatsAppEnvelope(
                kind="text",
                phone_number_id=phone_number_id,
                sender_id=sender_id,
                message_id=message_id,
                text=body,
            )
    return ParsedWhatsAppEnvelope(
        kind="non_text",
        phone_number_id=phone_number_id,
        sender_id=sender_id,
        message_id=message_id,
        text="",
    )


def _text_body(text_block: object) -> str | None:
    if not isinstance(text_block, dict):
        return None
    body = text_block.get("body")
    if isinstance(body, str):
        return body
    return None


def _string(value: object) -> str:
    if isinstance(value, str):
        return value
    return ""


def _loads_json(raw_body: bytes) -> object | None:
    try:
        return json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _payload_phone_number_id(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    entry = _first_mapping(payload.get("entry"))
    if entry is None:
        return ""
    change = _first_mapping(entry.get("changes"))
    if change is None:
        return ""
    value = change.get("value")
    if not isinstance(value, dict):
        return ""
    metadata = value.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    phone_number_id = metadata.get("phone_number_id")
    if isinstance(phone_number_id, str):
        return phone_number_id.strip()
    return ""


def _binding_for_signature(raw_body: bytes, signature: str, candidates: list):
    for candidate in candidates:
        secret = read_webhook_secret(candidate)
        if verify_jvconnect_signature(raw_body, secret, signature):
            return candidate
    return None


async def _sender_is_verified(sender_id: str) -> bool:
    from app.agentive.services.channel_identity import resolve_channel_identity

    if not sender_id:
        return False
    resolved = await resolve_channel_identity("whatsapp", sender_id)
    if resolved:
        return True
    if sender_id.startswith("+"):
        return False
    resolved = await resolve_channel_identity("whatsapp", f"+{sender_id}")
    return resolved is not None


async def _store_receipt(
    binding_id: str,
    meta_message_id: str,
    kind: str,
    status: str,
    last_error: str,
):
    from app.models.whatsapp_inbound import WhatsAppInboundReceipt
    from app.utils.time import utc_now_iso

    return await WhatsAppInboundReceipt.create(
        binding_id=binding_id,
        meta_message_id=meta_message_id,
        kind=kind,
        status=status,
        last_error=last_error,
        created_at=utc_now_iso(),
    )


async def _send_notice(binding, to: str, body: str) -> str:
    from app.agentive.clients.jvconnect_whatsapp import send_text

    api_key = read_api_key(binding)
    if not api_key:
        return "Could not read the stored API key"
    try:
        result = await send_text(
            binding.jvconnect_base_url,
            api_key,
            to,
            body,
            client=None,
        )
    except Exception:
        logger.warning("whatsapp notice send failed")
        return "WhatsApp send failed"
    if result.ok:
        return ""
    error = result.error or "WhatsApp send failed"
    if api_key in error:
        return "WhatsApp send failed"
    return error[:300]


async def _send_notice_and_store(binding, envelope, body: str, kind: str) -> None:
    receipt = await _store_receipt(
        binding.id,
        envelope.message_id,
        kind,
        "pending",
        "",
    )
    error = await _send_notice(binding, envelope.sender_id, body)
    receipt.status = "notice_sent" if not error else "send_failed"
    receipt.last_error = error
    await receipt.save()


async def _dispatch_verified(binding, envelope) -> dict:
    from app.models.whatsapp_inbound import WhatsAppInboundReceipt

    if not binding.enabled:
        return {"ok": True}
    if envelope.kind == "ignore" or not envelope.message_id:
        return {"ok": True}
    existing = await WhatsAppInboundReceipt.find(
        binding_id=binding.id,
        meta_message_id=envelope.message_id,
    )
    if existing:
        return {"ok": True}
    if envelope.kind == "non_text":
        await _send_notice_and_store(binding, envelope, _TEXT_ONLY_NOTICE, "non_text")
        return {"ok": True}
    if not await _sender_is_verified(envelope.sender_id):
        await _send_notice_and_store(binding, envelope, _LINK_NOTICE, "text")
        return {"ok": True}
    await _store_receipt(
        binding.id,
        envelope.message_id,
        "text",
        "accepted",
        "",
    )
    return {"ok": True}


async def receive_whatsapp_webhook(raw_body: bytes, signature: str) -> dict:
    """Verify a jvconnect webhook and store a receipt when the message is new."""
    from app.agentive.nodes import WhatsAppChannelBinding

    if not isinstance(raw_body, (bytes, bytearray)):
        raise MissingAuthenticationError(message="Invalid webhook signature")
    payload = _loads_json(bytes(raw_body))
    phone_number_id = _payload_phone_number_id(payload)
    if not phone_number_id:
        raise MissingAuthenticationError(message="Invalid webhook signature")
    candidates = await WhatsAppChannelBinding.find(phone_number_id=phone_number_id)
    binding = _binding_for_signature(bytes(raw_body), signature, candidates)
    if binding is None:
        raise MissingAuthenticationError(message="Invalid webhook signature")
    envelope = parse_whatsapp_envelope(payload if isinstance(payload, dict) else {})
    return await _dispatch_verified(binding, envelope)
