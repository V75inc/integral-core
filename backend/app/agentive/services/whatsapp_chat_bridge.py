"""Admit one verified owner's WhatsApp text onto a native chat thread."""

from __future__ import annotations

import hashlib
import logging

from app.agentive.services.whatsapp_ingress import ParsedWhatsAppEnvelope

logger = logging.getLogger(__name__)

_PROVIDER_ID = "integral_native"


def _client_request_id(meta_message_id: str) -> str:
    digest = hashlib.sha256(meta_message_id.encode("utf-8")).hexdigest()
    return f"wa.{digest}"


def _session_id(workspace_id: str, e164: str) -> str:
    return f"wa:{workspace_id}:{e164}"


def _recipient_from_session(session: str) -> str:
    parts = session.split(":", 2)
    if len(parts) != 3 or parts[0] != "wa":
        return ""
    return parts[2].strip()


def _confirmation_body(verb: str, result: dict) -> str:
    if not result.get("ok"):
        return "Could not apply that."
    if verb == "revoke":
        return "Rejected."
    return "Approved."


async def send_channel_text(binding, to: str, body: str) -> str:
    """Send one WhatsApp text. Return an error string, or empty on success."""
    from app.agentive.clients.jvconnect_whatsapp import send_text
    from app.agentive.services.whatsapp_channel_binding import read_api_key

    try:
        api_key = read_api_key(binding)
    except Exception:
        logger.warning("whatsapp channel key read failed")
        return "Could not read the stored API key"
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
        logger.warning("whatsapp channel send failed")
        return "WhatsApp send failed"
    if result.ok:
        return ""
    error = result.error or "WhatsApp send failed"
    if api_key in error:
        return "WhatsApp send failed"
    return error[:300]


def _public_assistant_text(parts) -> str:
    chunks = []
    for part in parts or []:
        if not isinstance(part, dict) or part.get("type") != "text":
            continue
        value = part.get("text")
        if isinstance(value, str):
            chunks.append(value)
    text = "".join(chunks)
    if not text.strip():
        return ""
    return text


async def _stored_client_request_id(item) -> str:
    from app.models.harness_records import HarnessTurnInputRecord

    records = await HarnessTurnInputRecord.find(
        {"work_item_id": getattr(item, "work_item_id", "")}
    )
    for record in records:
        if (
            record.principal_id == item.principal_id
            and record.workspace_id == item.workspace_id
            and record.thread_id == item.thread_id
            and isinstance(record.client_request_id, str)
            and record.client_request_id.startswith("wa.")
        ):
            return record.client_request_id
    return ""


async def _receipt_for_delivery(binding_id: str, item):
    from app.models.whatsapp_inbound import WhatsAppInboundReceipt

    receipts = await WhatsAppInboundReceipt.find(binding_id=binding_id)
    client_request_id = await _stored_client_request_id(item)
    if client_request_id:
        matched = [
            receipt
            for receipt in receipts
            if receipt.meta_message_id
            and _client_request_id(receipt.meta_message_id) == client_request_id
        ]
        if matched:
            return matched[0]
    accepted = [
        receipt
        for receipt in receipts
        if receipt.status == "accepted" and receipt.kind in {"", "text"}
    ]
    if len(accepted) == 1:
        return accepted[0]
    return None


async def _record_delivery_error(binding_id: str, item, error: str) -> None:
    receipt = await _receipt_for_delivery(binding_id, item)
    if receipt is None:
        logger.warning(
            "whatsapp delivery failed without a matching receipt for work item %s",
            getattr(item, "work_item_id", ""),
        )
        return
    receipt.status = "send_failed"
    receipt.last_error = (error or "WhatsApp send failed")[:300]
    await receipt.save()


async def _committed_assistant_text(item, thread) -> str:
    from app.models.edges import CONTAINS
    from app.models.nodes import ChatMessage
    from app.services.chat_turn_transcript import _message_id

    if thread.user_id != item.principal_id or thread.workspace_id != item.workspace_id:
        return ""
    message = await ChatMessage.get(_message_id(item.work_item_id))
    accepted = str(
        (getattr(item, "input_payload", None) or {}).get("accepted_message_id") or ""
    )
    if (
        message is None
        or message.thread_id != thread.id
        or message.role != "assistant"
        or not accepted
        or message.parent_id != accepted
    ):
        return ""
    graph = await thread.get_context()
    edges = await graph.find_edges_between(thread.id, message.id, edge_class=CONTAINS)
    if not edges:
        return ""
    return _public_assistant_text(message.parts)


async def deliver_terminal_whatsapp_reply(item) -> None:
    """Send the committed assistant text for a finished WhatsApp turn."""
    from app.models.nodes import ChatThread
    from app.schemas.agentive.work import TERMINAL_WORK_STATUSES

    if getattr(item, "kind", "") != "chat_turn":
        return
    if getattr(item, "status", "") not in TERMINAL_WORK_STATUSES:
        return
    thread_id = str(getattr(item, "thread_id", "") or "")
    if not thread_id:
        return
    thread = await ChatThread.get(thread_id)
    if thread is None:
        return
    session = str(thread.provider_session_id or "")
    if not session.startswith("wa:"):
        return
    recipient = _recipient_from_session(session)
    if not recipient:
        return
    text = await _committed_assistant_text(item, thread)
    if not text:
        return
    from app.agentive.services.whatsapp_channel_binding import binding_for_workspace

    binding = await binding_for_workspace(str(item.workspace_id or ""))
    if binding is None or not binding.enabled:
        return
    # Delivery is a new disclosure boundary, after queued work may have waited.
    # Recheck the current linked identity and workspace membership before sending.
    from app.agentive.services.whatsapp_ingress import (
        _accessible_workspaces,
        _sender_principal,
        _verified_sender,
    )

    identity = await _verified_sender(recipient)
    if identity is None or await _sender_principal(identity) != item.principal_id:
        return
    workspaces = await _accessible_workspaces(binding, item.principal_id)
    if item.workspace_id not in {workspace.id for workspace in workspaces}:
        return
    error = await send_channel_text(binding, recipient, text)
    if error:
        await _record_delivery_error(binding.id, item, error)


async def _agent_for_new_thread(user_id: str, workspace_id: str) -> str:
    from app.api.ai_chat import _resolve_agent_for_new_thread
    from app.services.chat_providers.registry import get_registry

    provider = get_registry().get(_PROVIDER_ID)
    if provider is None:
        raise RuntimeError("integral_native provider is not registered")
    return await _resolve_agent_for_new_thread(
        user_id=user_id,
        workspace_id=workspace_id,
        provider=provider,
        body_agent_id="",
    )


async def _dedicated_thread(user_id: str, workspace_id: str, e164: str):
    from app.services.chat_threads import (
        create_thread,
        get_thread_by_session,
        update_provider_session,
    )

    session_id = _session_id(workspace_id, e164)
    existing = await get_thread_by_session(session_id)
    if (
        existing is not None
        and existing.provider_id == _PROVIDER_ID
        and existing.user_id == user_id
        and existing.workspace_id == workspace_id
        and not existing.archived
    ):
        await update_provider_session(existing, session_id)
        return existing
    thread = await create_thread(
        user_id=user_id,
        provider_id=_PROVIDER_ID,
        workspace_id=workspace_id,
        agent_id=await _agent_for_new_thread(user_id, workspace_id),
    )
    await update_provider_session(thread, session_id)
    return thread


async def _store_text_receipt(
    binding_id: str,
    meta_message_id: str,
    *,
    status: str,
    last_error: str = "",
):
    from app.models.whatsapp_inbound import WhatsAppInboundReceipt
    from app.utils.time import utc_now_iso

    return await WhatsAppInboundReceipt.create(
        binding_id=binding_id,
        meta_message_id=meta_message_id,
        kind="text",
        status=status,
        last_error=last_error,
        created_at=utc_now_iso(),
    )


async def _apply_single_approval(
    binding,
    envelope: ParsedWhatsAppEnvelope,
    thread,
    *,
    user_id: str,
    workspace_id: str,
) -> bool:
    from app.agentive.services.approval_decisions import decide_staged_write
    from app.agentive.services.approval_intent import parse_approval_intent
    from app.agentive.staging import list_unresolved_for_session

    pending = await list_unresolved_for_session(
        user_id,
        thread.provider_session_id,
    )
    workspace_id = thread.workspace_id
    pending = [
        staged
        for staged in pending
        if getattr(staged, "workspace_id", None) == workspace_id
    ]
    intents = parse_approval_intent(envelope.text, pending=pending)
    if not intents or len(intents) != 1:
        return False
    intent = intents[0]
    result = await decide_staged_write(
        principal_id=user_id,
        proposal_id=intent.token,
        decision="approve" if intent.verb == "bless" else "reject",
        source="channel",
        workspace_id=workspace_id,
        conversation_id=thread.provider_session_id,
        thread_id=thread.id,
    )
    receipt = await _store_text_receipt(
        binding.id,
        envelope.message_id,
        status="accepted",
    )
    error = await send_channel_text(
        binding,
        envelope.sender_id,
        _confirmation_body(intent.verb, result),
    )
    if error:
        receipt.status = "send_failed"
        receipt.last_error = error
        await receipt.save()
    return True


async def _enqueue_turn(
    binding,
    envelope: ParsedWhatsAppEnvelope,
    thread,
    *,
    user_id: str,
    workspace_id: str,
) -> None:
    from app.schemas.agentive.work import ChatTurnSubmissionRequest
    from app.services.chat_turn_submissions import submit_chat_turn

    await submit_chat_turn(
        ChatTurnSubmissionRequest(
            principal_id=user_id,
            workspace_id=workspace_id,
            thread_id=thread.id,
            client_request_id=_client_request_id(envelope.message_id),
            parts=[{"type": "text", "text": envelope.text}],
        )
    )
    await _store_text_receipt(
        binding.id,
        envelope.message_id,
        status="accepted",
    )


async def admit_owner_whatsapp_text(
    binding,
    envelope: ParsedWhatsAppEnvelope,
    *,
    e164: str,
    user_id: str,
    workspace_id: str,
) -> None:
    """Enqueue one native turn, or apply one channel approval, for this text."""
    thread = await _dedicated_thread(user_id, workspace_id, e164)
    if await _apply_single_approval(
        binding, envelope, thread, user_id=user_id, workspace_id=workspace_id
    ):
        return
    await _enqueue_turn(
        binding, envelope, thread, user_id=user_id, workspace_id=workspace_id
    )
