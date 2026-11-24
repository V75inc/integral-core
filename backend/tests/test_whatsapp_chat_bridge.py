"""WhatsApp owner text is admitted on one native thread."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from app.agentive.clients.jvconnect_whatsapp import JvconnectResult
from app.schemas.agentive.work import ChatTurnSubmissionReceipt
from tests.test_whatsapp_ingress import (
    _PHONE,
    _envelope,
    _jvconnect_signature,
    _post_webhook,
    _raw_payload,
    _ready_binding,
    _text_message,
)

_SENDER = "15551212"
_E164 = "+15551212"


def _session_for(workspace_id: str) -> str:
    """Session id for one sender in one workspace."""
    return f"wa:{workspace_id}:{_E164}"


@pytest.fixture
def enc_key(monkeypatch):
    """32-byte hex credential key for this test."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "ab" * 32)
    return "ab" * 32


@pytest.fixture
def sent_texts(monkeypatch):
    """Capture send_text calls without opening a socket."""
    calls = []

    async def fake_send(base_url, api_key, to, body, *, client=None):
        calls.append({"base_url": base_url, "to": to, "body": body, "client": client})
        return JvconnectResult(ok=True, status_code=200, data={}, error="")

    monkeypatch.setattr(
        "app.agentive.clients.jvconnect_whatsapp.send_text",
        fake_send,
    )
    return calls


def _patch_submit(monkeypatch, *, raises: BaseException | None = None):
    """Record submit_chat_turn calls and skip the real queue."""
    calls = []

    async def fake_submit(request):
        calls.append(request)
        if raises is not None:
            raise raises
        return ChatTurnSubmissionReceipt(
            client_request_id=request.client_request_id,
            message_id="msg-test",
            work_item_id="work-test",
            status="queued",
        )

    monkeypatch.setattr(
        "app.services.chat_turn_submissions.submit_chat_turn",
        fake_submit,
    )
    return calls


def _patch_decisions(monkeypatch):
    """Record decide_staged_write calls and skip execution."""
    calls = []

    async def fake_decide(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "state": "blessed"}

    monkeypatch.setattr(
        "app.agentive.services.approval_decisions.decide_staged_write",
        fake_decide,
    )
    return calls


def _patch_owner(monkeypatch, user):
    """Resolve the sender as this user's verified WhatsApp identity."""
    graph_id = user.id

    async def resolve(channel, channel_user_id):
        if channel == "whatsapp" and channel_user_id == _E164:
            return {
                "user_id": graph_id,
                "channel_user_id": channel_user_id,
                "verified": True,
            }
        return None

    monkeypatch.setattr(
        "app.agentive.services.channel_identity.resolve_channel_identity",
        resolve,
    )


def _signed_text(body: str, message_id: str) -> tuple[bytes, str]:
    """Raw webhook bytes and a valid signature for one text message."""
    raw = _raw_payload(
        _envelope(
            [_text_message(body, sender=_SENDER, message_id=message_id)],
            phone_number_id=_PHONE,
        )
    )
    return raw, _jvconnect_signature(raw, "whsec_test")


async def _threads_for_session(workspace_id: str):
    """Native threads stored for this sender's WhatsApp session."""
    from app.models.nodes import ChatThread

    found = await ChatThread.find(
        {"context.provider_session_id": _session_for(workspace_id)}
    )
    return [
        thread
        for thread in found
        if getattr(thread, "provider_id", "") == "integral_native"
    ]


def _request_id(meta_message_id: str) -> str:
    """Expected submission id for a raw Meta message id."""
    digest = hashlib.sha256(meta_message_id.encode("utf-8")).hexdigest()
    return "wa." + digest


@pytest.mark.asyncio
async def test_first_inbound_creates_native_thread_and_second_reuses_it(
    enc_key, test_user, client, sent_texts, monkeypatch
):
    """The first text creates wa:{workspace}:{e164}; the next text reuses it."""
    submits = _patch_submit(monkeypatch)
    _patch_owner(monkeypatch, test_user)
    binding = await _ready_binding(test_user)
    first_raw, first_sig = _signed_text("hello", "wamid.first")
    second_raw, second_sig = _signed_text("again", "wamid.second")

    first = await _post_webhook(
        client, first_raw, first_sig, header="X-Jvconnect-Signature"
    )
    created = await _threads_for_session(binding.workspace_id)
    second = await _post_webhook(
        client, second_raw, second_sig, header="X-Jvconnect-Signature"
    )
    reused = await _threads_for_session(binding.workspace_id)

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(created) == 1
    thread = created[0]
    assert thread.provider_id == "integral_native"
    assert thread.workspace_id == binding.workspace_id
    assert thread.provider_session_id == _session_for(binding.workspace_id)
    assert thread.user_id == binding.user_id
    assert len(reused) == 1
    assert reused[0].id == thread.id
    assert [call.thread_id for call in submits] == [thread.id, thread.id]
    from app.services.chat_providers.registry import get_registry

    provider = get_registry().get("integral_native")
    catalog = await provider.list_agents()
    assert thread.agent_id == catalog[0]["id"]


@pytest.mark.asyncio
async def test_normal_text_enqueues_one_turn_and_returns_200(
    enc_key, test_user, client, sent_texts, monkeypatch
):
    """A normal owner text calls submit_chat_turn once and the webhook is 200."""
    from app.agentive.services.whatsapp_channel_binding import workspace_line_grant_id

    submits = _patch_submit(monkeypatch)
    _patch_decisions(monkeypatch)
    _patch_owner(monkeypatch, test_user)
    binding = await _ready_binding(test_user)
    meta_id = "wamid.ABC/xyz+1"
    raw, signature = _signed_text("hello from whatsapp", meta_id)

    response = await _post_webhook(
        client, raw, signature, header="X-Jvconnect-Signature"
    )
    duplicate = await _post_webhook(
        client, raw, signature, header="X-Jvconnect-Signature"
    )

    assert response.status_code == 200
    assert duplicate.status_code == 200
    assert len(submits) == 1
    request = submits[0]
    assert request.parts == [{"type": "text", "text": "hello from whatsapp"}]
    import hashlib

    grant_id = await workspace_line_grant_id(binding.workspace_id, binding.id)
    prefix = hashlib.sha256(grant_id.encode("utf-8")).hexdigest()[:32]
    assert request.client_request_id == f"wa.grant.{prefix}.{_request_id(meta_id)[3:]}"
    assert sent_texts == []
    from app.models.whatsapp_inbound import WhatsAppInboundReceipt

    receipts = await WhatsAppInboundReceipt.find(meta_message_id=meta_id)
    assert len(receipts) == 1
    assert receipts[0].status == "accepted"


@pytest.mark.asyncio
async def test_single_approval_decides_on_channel_and_skips_the_turn(
    enc_key, test_user, client, sent_texts, monkeypatch
):
    """One parsed approval decides with source channel and does not enqueue."""
    from app.agentive.staging import StagedChange

    submits = _patch_submit(monkeypatch)
    decisions = _patch_decisions(monkeypatch)
    _patch_owner(monkeypatch, test_user)
    binding = await _ready_binding(test_user)
    now = datetime.now(timezone.utc)
    pending = StagedChange(
        token="tok-wa-1",
        user_id=binding.user_id,
        session_id=_session_for(binding.workspace_id),
        kind="entry.update",
        summary="Update a record",
        diff_human="name",
        diff_machine={},
        payload={},
        created_at=now,
        expires_at=now + timedelta(minutes=10),
        workspace_id=binding.workspace_id,
    )

    async def fake_list(user_id, session_id):
        assert user_id == binding.user_id
        assert session_id == _session_for(binding.workspace_id)
        return [pending]

    monkeypatch.setattr(
        "app.agentive.staging.list_unresolved_for_session",
        fake_list,
    )
    raw, signature = _signed_text("yes", "wamid.approve")

    response = await _post_webhook(
        client, raw, signature, header="X-Jvconnect-Signature"
    )

    assert response.status_code == 200
    assert submits == []
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision["source"] == "channel"
    assert decision["principal_id"] == binding.user_id
    assert decision["workspace_id"] == binding.workspace_id
    assert decision["thread_id"]
    assert decision["conversation_id"] == _session_for(binding.workspace_id)
    assert decision["proposal_id"] == "tok-wa-1"
    assert decision["decision"] == "approve"
    assert [call["body"] for call in sent_texts] == ["Approved."]
    assert sent_texts[0]["to"] == _SENDER


@pytest.mark.asyncio
async def test_disabled_binding_does_not_submit_chat(
    enc_key, test_user, client, sent_texts, monkeypatch
):
    """A disabled binding does not call submit_chat_turn."""
    submits = _patch_submit(monkeypatch)
    decisions = _patch_decisions(monkeypatch)
    _patch_owner(monkeypatch, test_user)
    await _ready_binding(test_user, enabled=False)
    raw, signature = _signed_text("hello", "wamid.disabled")

    response = await _post_webhook(
        client, raw, signature, header="X-Jvconnect-Signature"
    )

    assert response.status_code == 200
    assert submits == []
    assert decisions == []
    assert sent_texts == []


@pytest.mark.asyncio
async def test_unverified_sender_does_not_submit_chat(
    enc_key, test_user, client, sent_texts, monkeypatch
):
    """An unverified sender does not call submit_chat_turn."""
    submits = _patch_submit(monkeypatch)
    decisions = _patch_decisions(monkeypatch)

    async def resolve(channel, channel_user_id):
        return None

    monkeypatch.setattr(
        "app.agentive.services.channel_identity.resolve_channel_identity",
        resolve,
    )
    await _ready_binding(test_user)
    raw, signature = _signed_text("hello", "wamid.unknown")

    response = await _post_webhook(
        client, raw, signature, header="X-Jvconnect-Signature"
    )

    assert response.status_code == 200
    assert submits == []
    assert decisions == []
    assert [call["body"] for call in sent_texts] == [
        "Link your number in Integral settings"
    ]


@pytest.mark.asyncio
async def test_enqueue_failure_does_not_consume_the_meta_id(
    enc_key, test_user, client, sent_texts, monkeypatch
):
    """A failed enqueue leaves the Meta id free so the forward can retry."""
    _patch_submit(monkeypatch, raises=RuntimeError("queue down"))
    _patch_owner(monkeypatch, test_user)
    await _ready_binding(test_user)
    raw, signature = _signed_text("hello", "wamid.retry")

    with pytest.raises(RuntimeError, match="queue down"):
        await _post_webhook(client, raw, signature, header="X-Jvconnect-Signature")

    from app.models.whatsapp_inbound import WhatsAppInboundReceipt

    assert await WhatsAppInboundReceipt.find(meta_message_id="wamid.retry") == []


async def _terminal_turn(
    user,
    *,
    session_id: str,
    assistant_text: str | None,
    meta_message_id: str,
    enabled: bool = True,
    extra_meta_message_id: str = "",
):
    """Persist one finished native turn and its inbound receipt."""
    import uuid

    from app.agentive.work_models import WorkItem
    from app.models.harness_records import HarnessTurnInputRecord
    from app.models.nodes import ChatThread
    from app.models.whatsapp_inbound import WhatsAppInboundReceipt
    from app.services.chat_threads import (
        append_message,
        create_thread,
        update_provider_session,
    )
    from app.services.chat_turn_transcript import _message_id
    from app.utils.time import utc_now_iso

    binding = await _ready_binding(user, enabled=enabled)
    if session_id == "whatsapp":
        session_id = _session_for(binding.workspace_id)
    thread = await create_thread(
        user_id=binding.user_id,
        provider_id="integral_native",
        workspace_id=binding.workspace_id,
        agent_id="agent-test",
    )
    await update_provider_session(thread, session_id)
    thread = await ChatThread.get(thread.id)
    user_message = await append_message(
        thread=thread,
        role="user",
        parts=[{"type": "text", "text": "hello"}],
    )
    work_item_id = "wa-turn-" + uuid.uuid4().hex
    item = await WorkItem.create(
        id=f"o.WorkItem.{work_item_id}",
        work_item_id=work_item_id,
        kind="chat_turn",
        status="succeeded",
        principal_id=binding.user_id,
        workspace_id=binding.workspace_id,
        thread_id=thread.id,
        input_payload={"accepted_message_id": user_message.id},
        attempt=1,
        lease_fence=1,
    )
    parts = (
        [{"type": "text", "text": assistant_text}]
        if assistant_text is not None
        else [{"type": "tool-call", "toolCallId": "call-1", "toolName": "lookup"}]
    )
    await append_message(
        thread=thread,
        role="assistant",
        parts=parts,
        parent_id=user_message.id,
        message_id=_message_id(work_item_id),
    )
    await HarnessTurnInputRecord.create(
        principal_id=binding.user_id,
        workspace_id=binding.workspace_id,
        thread_id=thread.id,
        work_item_id=work_item_id,
        accepted_message_id=user_message.id,
        client_request_id=_request_id(meta_message_id),
    )
    receipt = await WhatsAppInboundReceipt.create(
        binding_id=binding.id,
        meta_message_id=meta_message_id,
        kind="text",
        status="accepted",
        last_error="",
        created_at=utc_now_iso(),
    )
    if extra_meta_message_id:
        await WhatsAppInboundReceipt.create(
            binding_id=binding.id,
            meta_message_id=extra_meta_message_id,
            kind="text",
            status="accepted",
            last_error="",
            created_at=utc_now_iso(),
        )
    return item, thread, binding, receipt


@pytest.mark.asyncio
async def test_succeeded_whatsapp_turn_sends_terminal_assistant_text(
    enc_key, test_user, sent_texts, monkeypatch
):
    """A succeeded wa: turn sends the committed assistant text once."""
    from app.agentive.services.whatsapp_chat_bridge import (
        deliver_terminal_whatsapp_reply,
    )
    from app.agentive.work_models import WorkItem

    _patch_owner(monkeypatch, test_user)
    item, _, _, receipt = await _terminal_turn(
        test_user,
        session_id="whatsapp",
        assistant_text="The ledger is balanced.",
        meta_message_id="wamid.reply",
    )

    await deliver_terminal_whatsapp_reply(item)

    assert [call["body"] for call in sent_texts] == ["The ledger is balanced."]
    assert [call["to"] for call in sent_texts] == [_E164]
    assert sent_texts[0]["base_url"] == "https://connect.example"
    reloaded = await WorkItem.get(item.id)
    assert reloaded is not None
    assert reloaded.status == "succeeded"
    stored = await receipt.__class__.get(receipt.id)
    assert stored is not None
    assert stored.status == "accepted"
    assert stored.last_error == ""


@pytest.mark.asyncio
async def test_non_whatsapp_thread_makes_no_jvconnect_call(
    enc_key, test_user, sent_texts
):
    """A thread session that does not start with wa: does not call send_text."""
    from app.agentive.services.whatsapp_chat_bridge import (
        deliver_terminal_whatsapp_reply,
    )

    item, _, _, _ = await _terminal_turn(
        test_user,
        session_id="native-session",
        assistant_text="This stays in the app.",
        meta_message_id="wamid.native",
    )

    await deliver_terminal_whatsapp_reply(item)

    assert sent_texts == []


@pytest.mark.asyncio
async def test_send_failure_keeps_work_item_and_records_receipt(
    enc_key, test_user, monkeypatch
):
    """A failed send leaves the WorkItem unchanged and does not send again."""
    from app.agentive.services.whatsapp_chat_bridge import (
        deliver_terminal_whatsapp_reply,
    )
    from app.agentive.work_models import WorkItem
    from app.models.whatsapp_inbound import WhatsAppInboundReceipt

    calls = []

    async def fake_send(base_url, api_key, to, body, *, client=None):
        calls.append({"base_url": base_url, "to": to, "body": body, "client": client})
        return JvconnectResult(
            ok=False, status_code=502, data={}, error="upstream down"
        )

    monkeypatch.setattr(
        "app.agentive.clients.jvconnect_whatsapp.send_text",
        fake_send,
    )
    _patch_owner(monkeypatch, test_user)
    item, _, binding, receipt = await _terminal_turn(
        test_user,
        session_id="whatsapp",
        assistant_text="The ledger is balanced.",
        meta_message_id="wamid.fail",
        extra_meta_message_id="wamid.other",
    )

    await deliver_terminal_whatsapp_reply(item)

    assert [call["body"] for call in calls] == ["The ledger is balanced."]
    assert all("delivery failed" not in call["body"].lower() for call in calls)
    reloaded = await WorkItem.get(item.id)
    assert reloaded is not None
    assert reloaded.status == "succeeded"
    stored = await WhatsAppInboundReceipt.get(receipt.id)
    assert stored is not None
    assert stored.status == "send_failed"
    assert stored.last_error
    assert "upstream down" in stored.last_error
    decoys = await WhatsAppInboundReceipt.find(
        binding_id=binding.id, meta_message_id="wamid.other"
    )
    assert len(decoys) == 1
    assert decoys[0].status == "accepted"
    assert decoys[0].last_error == ""


@pytest.mark.asyncio
async def test_whatsapp_turn_without_public_text_does_not_send(
    enc_key, test_user, sent_texts
):
    """A terminal turn with no public assistant text sends nothing."""
    from app.agentive.services.whatsapp_chat_bridge import (
        deliver_terminal_whatsapp_reply,
    )

    item, _, _, _ = await _terminal_turn(
        test_user,
        session_id="whatsapp",
        assistant_text=None,
        meta_message_id="wamid.notext",
    )

    await deliver_terminal_whatsapp_reply(item)

    assert sent_texts == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_claimed_work_delivers_after_chat_turn(monkeypatch):
    """Delivery is invoked only after the chat turn handler returns."""
    from types import SimpleNamespace

    from app.agentive.services.work_worker import _execute_claimed_work

    order = []
    finished = SimpleNamespace(
        kind="chat_turn", status="succeeded", work_item_id="wi-order"
    )

    async def fake_handle(item, *, worker_id, lease_seconds):
        order.append("handle")
        return finished

    async def fake_deliver(item):
        order.append("deliver")
        assert item is finished

    monkeypatch.setattr(
        "app.agentive.services.work_worker._handle_chat_turn",
        fake_handle,
    )
    monkeypatch.setattr(
        "app.agentive.services.whatsapp_chat_bridge.deliver_terminal_whatsapp_reply",
        fake_deliver,
    )
    claimed = SimpleNamespace(
        kind="chat_turn", work_item_id="wi-order", status="running"
    )

    result = await _execute_claimed_work(claimed, worker_id="worker", lease_seconds=30)

    assert order == ["handle", "deliver"]
    assert result is finished


@pytest.mark.asyncio
async def test_terminal_delivery_requires_current_link(enc_key, test_user, sent_texts):
    """A queued reply is withheld when its recipient has no current verified link."""
    from app.agentive.services.whatsapp_chat_bridge import (
        deliver_terminal_whatsapp_reply,
    )

    item, _, _, _ = await _terminal_turn(
        test_user,
        session_id="whatsapp",
        assistant_text="Private reply",
        meta_message_id="wamid.revoked-link",
    )
    await deliver_terminal_whatsapp_reply(item)
    assert sent_texts == []


@pytest.mark.asyncio
async def test_replaced_whatsapp_grant_cannot_receive_a_queued_reply(
    enc_key, test_user, sent_texts, monkeypatch
):
    """Revocation fences a queued reply even when the same line is granted again."""
    from app.agentive.services.whatsapp_channel_binding import (
        disconnect_workspace_whatsapp_line,
        grant_whatsapp_line,
        workspace_line_grant_id,
    )
    from app.agentive.services.whatsapp_chat_bridge import (
        _client_request_id,
        deliver_terminal_whatsapp_reply,
    )
    from app.models.harness_records import HarnessTurnInputRecord

    _patch_owner(monkeypatch, test_user)
    item, _, binding, _ = await _terminal_turn(
        test_user,
        session_id="whatsapp",
        assistant_text="Original authorization only.",
        meta_message_id="wamid.old-grant",
    )
    original = await workspace_line_grant_id(binding.workspace_id, binding.id)
    assert original
    record = await HarnessTurnInputRecord.find_one(work_item_id=item.work_item_id)
    record.client_request_id = _client_request_id("wamid.old-grant", original)
    await record.save()

    disconnected = await disconnect_workspace_whatsapp_line(
        binding.user_id, binding.workspace_id, binding.id
    )
    await grant_whatsapp_line(binding.workspace_id, disconnected, binding.user_id)
    disconnected.enabled = True
    await disconnected.save()
    assert await workspace_line_grant_id(binding.workspace_id, binding.id) != original

    await deliver_terminal_whatsapp_reply(item)

    assert sent_texts == []
