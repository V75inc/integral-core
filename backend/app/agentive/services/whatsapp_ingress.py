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
_LINKED_NOTICE = "Number linked. Send a message to continue."
_NO_WORKSPACE_NOTICE = (
    "This number isn't a member of a workspace on this WhatsApp line."
)
_LINE_CHOICE = "whatsapp_line_workspace"


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


def _e164(phone: str) -> str:
    cleaned = phone.strip()
    if not cleaned:
        return ""
    if cleaned.startswith("+"):
        return cleaned
    return f"+{cleaned}"


async def _verified_sender(sender_id: str) -> dict | None:
    from app.agentive.services.channel_identity import resolve_channel_identity

    if not sender_id:
        return None
    resolved = await resolve_channel_identity("whatsapp", sender_id)
    if resolved is None and not sender_id.startswith("+"):
        resolved = await resolve_channel_identity("whatsapp", f"+{sender_id}")
    if not resolved:
        return None
    return resolved


async def _sender_principal(resolved: dict) -> str:
    from app.services.permissions import get_user_node

    candidate = str(resolved.get("principal_id") or resolved.get("user_id") or "")
    if not candidate:
        return ""
    user = await get_user_node(candidate)
    if user is None:
        return ""
    return str(getattr(user, "user_id", "") or user.id)


async def _accessible_workspaces(binding, principal: str) -> list:
    from app.agentive.services.whatsapp_channel_binding import granted_workspace_ids
    from app.models.nodes import Workspace
    from app.services.workspace_permissions import user_in_workspace_member_pool

    workspaces = []
    for workspace_id in await granted_workspace_ids(binding):
        if not await user_in_workspace_member_pool(principal, workspace_id):
            continue
        workspace = await Workspace.get(workspace_id)
        if workspace is not None:
            workspaces.append(workspace)
    workspaces.sort(
        key=lambda workspace: (
            (getattr(workspace, "name", "") or "").casefold(),
            workspace.id,
        )
    )
    return workspaces


def _picker_body(workspaces: list) -> str:
    lines = [
        f"{index}. {getattr(workspace, 'name', '') or 'Workspace'}"
        for index, workspace in enumerate(workspaces, start=1)
    ]
    joined = "\n".join(lines)
    return (
        "Which workspace?\n"
        f"{joined}\n"
        "Reply with a number. Send switch to choose again."
    )


async def _identity_record(resolved: dict):
    from app.agentive.nodes import ChannelIdentity

    identity_id = str(resolved.get("identity_id") or "")
    if not identity_id:
        return None
    return await ChannelIdentity.get(identity_id)


def _saved_workspace(record, phone_number_id: str) -> str:
    if record is None:
        return ""
    raw = (getattr(record, "preferences", {}) or {}).get(_LINE_CHOICE)
    if not isinstance(raw, dict):
        return ""
    return str(raw.get(phone_number_id) or "")


async def _store_workspace_choice(
    record, phone_number_id: str, workspace_id: str
) -> None:
    if record is None:
        return
    prefs = dict(getattr(record, "preferences", {}) or {})
    choices = dict(prefs.get(_LINE_CHOICE) or {})
    if workspace_id:
        choices[phone_number_id] = workspace_id
    else:
        choices.pop(phone_number_id, None)
    prefs[_LINE_CHOICE] = choices
    record.preferences = prefs
    await record.save()


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


async def _show_typing(binding, message_id: str) -> None:
    """Best-effort typing indicator. A failure does not change the reply."""
    from app.agentive.clients.jvconnect_whatsapp import send_typing_indicator

    if not message_id:
        return
    api_key = read_api_key(binding)
    if not api_key:
        return
    try:
        result = await send_typing_indicator(
            binding.jvconnect_base_url,
            api_key,
            message_id,
            client=None,
        )
    except Exception:
        logger.warning("whatsapp typing indicator failed")
        return
    if not result.ok:
        logger.info("whatsapp typing indicator not shown")


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
        await _show_typing(binding, envelope.message_id)
        await _send_notice_and_store(binding, envelope, _TEXT_ONLY_NOTICE, "non_text")
        return {"ok": True}
    from app.agentive.services.channel_identity import verify_pending_whatsapp_sender

    claimed = await verify_pending_whatsapp_sender(envelope.sender_id, envelope.text)
    if claimed:
        await _show_typing(binding, envelope.message_id)
        await _send_notice_and_store(binding, envelope, _LINKED_NOTICE, "text")
        return {"ok": True}
    identity = await _verified_sender(envelope.sender_id)
    if identity is None:
        await _show_typing(binding, envelope.message_id)
        await _send_notice_and_store(binding, envelope, _LINK_NOTICE, "text")
        return {"ok": True}
    principal = await _sender_principal(identity)
    workspaces = await _accessible_workspaces(binding, principal) if principal else []
    if not workspaces:
        await _show_typing(binding, envelope.message_id)
        await _send_notice_and_store(binding, envelope, _NO_WORKSPACE_NOTICE, "text")
        return {"ok": True}
    record = await _identity_record(identity)
    text = (envelope.text or "").strip()
    saved = _saved_workspace(record, binding.phone_number_id)
    workspace_ids = [workspace.id for workspace in workspaces]
    if len(workspaces) > 1 and text.lower() == "switch":
        await _store_workspace_choice(record, binding.phone_number_id, "")
        await _show_typing(binding, envelope.message_id)
        await _send_notice_and_store(
            binding, envelope, _picker_body(workspaces), "text"
        )
        return {"ok": True}
    if len(workspaces) > 1 and saved not in workspace_ids:
        if text.isdigit():
            index = int(text)
            if 1 <= index <= len(workspaces):
                chosen = workspaces[index - 1]
                await _store_workspace_choice(
                    record, binding.phone_number_id, chosen.id
                )
                name = getattr(chosen, "name", "") or "Workspace"
                await _show_typing(binding, envelope.message_id)
                await _send_notice_and_store(
                    binding, envelope, f"Using {name}.", "text"
                )
                return {"ok": True}
        await _show_typing(binding, envelope.message_id)
        await _send_notice_and_store(
            binding, envelope, _picker_body(workspaces), "text"
        )
        return {"ok": True}
    workspace_id = workspaces[0].id if len(workspaces) == 1 else saved
    e164 = _e164(str(identity.get("channel_user_id") or envelope.sender_id))
    if not e164:
        await _store_receipt(
            binding.id,
            envelope.message_id,
            "text",
            "accepted",
            "",
        )
        return {"ok": True}
    from app.agentive.services.whatsapp_chat_bridge import admit_owner_whatsapp_text

    await _show_typing(binding, envelope.message_id)
    await admit_owner_whatsapp_text(
        binding,
        envelope,
        e164=e164,
        user_id=principal,
        workspace_id=workspace_id,
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
