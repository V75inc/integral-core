"""Unit tests for WhatsApp ingress signature checks and envelope parsing."""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import inspect
import json

import pytest

from app.agentive.services.whatsapp_ingress import (
    ParsedWhatsAppEnvelope,
    parse_whatsapp_envelope,
    verify_jvconnect_signature,
)

_SECRET = "whsec_test"
_BODY = b'{"object":"whatsapp_business_account"}'


def _jvconnect_signature(raw_body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return "sha256=" + digest


def _envelope(
    messages: list | None = None,
    *,
    include_messages: bool = True,
    statuses: list | None = None,
    phone_number_id: str = "123",
) -> dict:
    value: dict = {
        "metadata": {
            "phone_number_id": phone_number_id,
            "display_phone_number": "1555",
        }
    }
    if include_messages:
        value["messages"] = [] if messages is None else messages
    if statuses is not None:
        value["statuses"] = statuses
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": value}]}],
    }


def _text_message(
    body: str = "hello",
    sender: str = "15551212",
    message_id: str = "wamid.abc",
) -> dict:
    return {
        "from": sender,
        "id": message_id,
        "type": "text",
        "text": {"body": body},
    }


@pytest.mark.unit
def test_jvconnect_signature_verifies():
    """A body signed the jvconnect way verifies."""
    signature = _jvconnect_signature(_BODY, _SECRET)
    assert verify_jvconnect_signature(_BODY, _SECRET, signature) is True


@pytest.mark.unit
def test_forged_signature_returns_false():
    """A forged signature returns False."""
    signature = _jvconnect_signature(_BODY, _SECRET)
    assert verify_jvconnect_signature(_BODY + b" ", _SECRET, signature) is False
    other = _jvconnect_signature(_BODY, "other-secret")
    assert verify_jvconnect_signature(_BODY, _SECRET, other) is False


@pytest.mark.unit
def test_missing_signature_returns_false():
    """A missing signature returns False."""
    assert verify_jvconnect_signature(_BODY, _SECRET, "") is False


@pytest.mark.unit
def test_truncated_signature_returns_false():
    """A truncated signature returns False."""
    signature = _jvconnect_signature(_BODY, _SECRET)
    assert verify_jvconnect_signature(_BODY, _SECRET, signature[:16]) is False


@pytest.mark.unit
def test_wrong_length_signature_returns_false():
    """A wrong-length signature returns False."""
    assert verify_jvconnect_signature(_BODY, _SECRET, "sha256=" + ("a" * 80)) is False


@pytest.mark.unit
def test_empty_secret_returns_false():
    """An empty secret returns False."""
    signature = _jvconnect_signature(_BODY, "")
    assert verify_jvconnect_signature(_BODY, "", signature) is False


@pytest.mark.unit
def test_signature_without_prefix_returns_false():
    """A digest without the sha256= prefix returns False."""
    signature = _jvconnect_signature(_BODY, _SECRET).removeprefix("sha256=")
    assert verify_jvconnect_signature(_BODY, _SECRET, signature) is False


@pytest.mark.unit
def test_non_string_signature_returns_false():
    """A non-string signature returns False and does not raise."""
    assert verify_jvconnect_signature(_BODY, _SECRET, None) is False  # type: ignore[arg-type]


@pytest.mark.unit
def test_non_string_secret_returns_false():
    """A non-string secret returns False and does not raise."""
    signature = _jvconnect_signature(_BODY, _SECRET)
    assert verify_jvconnect_signature(_BODY, None, signature) is False  # type: ignore[arg-type]


@pytest.mark.unit
def test_non_bytes_body_returns_false():
    """A non-bytes body returns False and does not raise."""
    signature = _jvconnect_signature(_BODY, _SECRET)
    assert verify_jvconnect_signature("not-bytes", _SECRET, signature) is False  # type: ignore[arg-type]


@pytest.mark.unit
def test_text_message_yields_sender_phone_id_and_text():
    """A text message yields sender, phone_number_id, message id, and text."""
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {
                                "phone_number_id": "123",
                                "display_phone_number": "1555",
                            },
                            "messages": [
                                {
                                    "from": "15551212",
                                    "id": "wamid.abc",
                                    "type": "text",
                                    "text": {"body": "hello"},
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }
    parsed = parse_whatsapp_envelope(payload)
    assert isinstance(parsed, ParsedWhatsAppEnvelope)
    assert parsed.kind == "text"
    assert parsed.sender_id == "15551212"
    assert parsed.phone_number_id == "123"
    assert parsed.message_id == "wamid.abc"
    assert parsed.text == "hello"


@pytest.mark.parametrize(
    "message_type",
    ["image", "document", "audio", "video", "sticker", "reaction"],
)
@pytest.mark.unit
def test_media_and_other_message_types_are_non_text(message_type: str):
    """image, document, audio, and other message types are non_text."""
    payload = _envelope(
        [
            {
                "from": "15551212",
                "id": "wamid.abc",
                "type": message_type,
                message_type: {"id": "media-1"},
            }
        ]
    )
    parsed = parse_whatsapp_envelope(payload)
    assert parsed.kind == "non_text"
    assert parsed.sender_id == "15551212"
    assert parsed.phone_number_id == "123"
    assert parsed.message_id == "wamid.abc"
    assert parsed.text == ""


@pytest.mark.unit
def test_text_type_without_body_is_non_text():
    """A text message with no body is non_text."""
    payload = _envelope(
        [{"from": "15551212", "id": "wamid.abc", "type": "text", "text": {}}]
    )
    parsed = parse_whatsapp_envelope(payload)
    assert parsed.kind == "non_text"
    assert parsed.sender_id == "15551212"
    assert parsed.phone_number_id == "123"
    assert parsed.message_id == "wamid.abc"
    assert parsed.text == ""


@pytest.mark.unit
def test_only_the_first_message_is_read():
    """Later messages in the same payload are ignored."""
    payload = _envelope(
        [
            _text_message("first", sender="111", message_id="wamid.first"),
            _text_message("second", sender="222", message_id="wamid.second"),
        ]
    )
    parsed = parse_whatsapp_envelope(payload)
    assert parsed.kind == "text"
    assert parsed.text == "first"
    assert parsed.sender_id == "111"
    assert parsed.message_id == "wamid.first"


@pytest.mark.unit
def test_statuses_only_payload_is_ignore():
    """A statuses-only payload is ignore and does not raise."""
    payload = _envelope(
        include_messages=False,
        statuses=[{"id": "wamid.abc", "status": "delivered"}],
    )
    parsed = parse_whatsapp_envelope(payload)
    assert parsed.kind == "ignore"
    assert parsed.phone_number_id == ""
    assert parsed.sender_id == ""
    assert parsed.message_id == ""
    assert parsed.text == ""


@pytest.mark.unit
def test_empty_messages_array_is_ignore():
    """An empty messages array is ignore and does not raise."""
    parsed = parse_whatsapp_envelope(_envelope([]))
    assert parsed.kind == "ignore"
    with_statuses = _envelope(
        [],
        statuses=[{"id": "wamid.abc", "status": "sent"}],
    )
    assert parse_whatsapp_envelope(with_statuses).kind == "ignore"


@pytest.mark.parametrize("payload", [None, [], "nope", 5])
@pytest.mark.unit
def test_non_dict_payload_is_ignore(payload: object):
    """A non-dict payload is ignore and does not raise."""
    assert parse_whatsapp_envelope(payload).kind == "ignore"  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"entry": []},
        {"entry": [{}]},
        {"entry": [{"changes": []}]},
        {"entry": [{"changes": [{}]}]},
        {"entry": [{"changes": [{"value": {}}]}]},
        {"entry": "bad"},
        {"entry": [None]},
        {"entry": [{"changes": [{"value": {"messages": "bad"}}]}]},
        {"entry": [{"changes": [{"value": {"messages": [None]}}]}]},
    ],
)
@pytest.mark.unit
def test_missing_entry_or_changes_is_ignore(payload: dict):
    """Missing entry or changes is ignore and does not raise."""
    assert parse_whatsapp_envelope(payload).kind == "ignore"


@pytest.mark.unit
def test_parsed_envelope_is_frozen():
    """A parsed envelope cannot be mutated."""
    parsed = parse_whatsapp_envelope(_envelope([_text_message()]))
    with pytest.raises(dataclasses.FrozenInstanceError):
        parsed.text = "nope"  # type: ignore[misc]


@pytest.fixture
def enc_key(monkeypatch):
    """32-byte hex credential key for this test."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "ab" * 32)
    return "ab" * 32


_WEBHOOK = "/api/agentive/channels/whatsapp/webhook"
_PHONE = "109876543210"
_HOOK_SECRET = "whsec_test"


def _user_id(user) -> str:
    """Principal id stored on channel records."""
    return getattr(user, "user_id", None) or user.id


def _raw_payload(payload: dict) -> bytes:
    """Stable JSON bytes so the signature matches the request body."""
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


@pytest.fixture
def sent_texts(monkeypatch):
    """Capture send_text calls without opening a socket."""

    class _Calls(list):
        """Send calls plus typing-indicator calls."""

        typing: list

    calls = _Calls()
    calls.typing = []

    async def fake_send(base_url, api_key, to, body, *, client=None):
        calls.append(
            {
                "base_url": base_url,
                "to": to,
                "body": body,
                "client": client,
            }
        )
        from app.agentive.clients.jvconnect_whatsapp import JvconnectResult

        return JvconnectResult(ok=True, status_code=200, data={}, error="")

    async def fake_typing(base_url, api_key, message_id, *, client=None):
        calls.typing.append(
            {
                "base_url": base_url,
                "message_id": message_id,
                "client": client,
            }
        )
        from app.agentive.clients.jvconnect_whatsapp import JvconnectResult

        return JvconnectResult(ok=True, status_code=200, data={}, error="")

    monkeypatch.setattr(
        "app.agentive.clients.jvconnect_whatsapp.send_text",
        fake_send,
    )
    monkeypatch.setattr(
        "app.agentive.clients.jvconnect_whatsapp.send_typing_indicator",
        fake_typing,
    )
    return calls


async def _ready_binding(user, *, secret=_HOOK_SECRET, phone=_PHONE, enabled=True):
    """Persist one binding whose webhook secret and phone id are already set."""
    from app.agentive.services.whatsapp_channel_binding import (
        create_whatsapp_channel_binding,
        store_webhook_secret,
    )

    binding = await create_whatsapp_channel_binding(
        user_id=_user_id(user),
        jvconnect_base_url="https://connect.example",
        api_key="jvk_test_secret_value",
    )
    await store_webhook_secret(binding, secret)
    binding.phone_number_id = phone
    binding.display_phone = "+15550001111"
    binding.enabled = enabled
    binding.last_error = ""
    await binding.save()
    return binding


def _assert_no_authorization(client) -> None:
    """The webhook client must not send a bearer token."""
    names = {key.lower() for key in client.headers}
    assert "authorization" not in names


async def _post_webhook(client, raw: bytes, signature: str, *, header: str):
    """POST raw bytes with one signature header and no Authorization."""
    _assert_no_authorization(client)
    return await client.post(
        _WEBHOOK,
        content=raw,
        headers={"Content-Type": "application/json", header: signature},
    )


def _assert_no_chat_submit() -> None:
    """Ingress and the channel routes must not call the chat harness."""
    import app.agentive.api.channels as channels
    import app.agentive.services.whatsapp_ingress as ingress

    for module in (ingress, channels):
        assert "submit_chat_turn" not in inspect.getsource(module)
        assert "ChatThread" not in inspect.getsource(module)


@pytest.mark.asyncio
async def test_forged_webhook_signature_returns_401_and_writes_no_receipt(
    enc_key, test_user, client, sent_texts
):
    """A forged signature is 401 and does not create a WhatsAppInboundReceipt."""
    from app.agentive.nodes import WhatsAppChannelBinding
    from app.models.whatsapp_inbound import WhatsAppInboundReceipt

    binding = await _ready_binding(test_user)
    raw = _raw_payload(
        _envelope(
            [_text_message(message_id="wamid.forged")],
            phone_number_id=_PHONE,
        )
    )
    response = await _post_webhook(
        client,
        raw,
        "sha256=" + ("ab" * 32),
        header="X-Jvconnect-Signature",
    )

    assert response.status_code == 401
    assert await WhatsAppInboundReceipt.find() == []
    assert sent_texts == []
    reloaded = await WhatsAppChannelBinding.get(binding.id)
    assert reloaded is not None
    assert reloaded.enabled is True
    assert reloaded.last_error == ""
    assert reloaded.webhook_secret_ciphertext == binding.webhook_secret_ciphertext
    _assert_no_chat_submit()


@pytest.mark.asyncio
async def test_repeated_meta_message_id_returns_200_and_sends_once(
    enc_key, test_user, client, sent_texts
):
    """A repeated Meta message id returns 200 and send_text runs once."""
    from app.models.whatsapp_inbound import WhatsAppInboundReceipt

    await _ready_binding(test_user)
    raw = _raw_payload(
        _envelope(
            [_text_message("hello", sender="15551212", message_id="wamid.repeat")],
            phone_number_id=_PHONE,
        )
    )
    signature = _jvconnect_signature(raw, _HOOK_SECRET)
    first = await _post_webhook(client, raw, signature, header="X-Jvconnect-Signature")
    second = await _post_webhook(client, raw, signature, header="X-Jvconnect-Signature")

    assert first.status_code == 200
    assert second.status_code == 200
    assert [call["body"] for call in sent_texts] == [
        "Link your number in Integral settings"
    ]
    assert [call["message_id"] for call in sent_texts.typing] == ["wamid.repeat"]
    receipts = await WhatsAppInboundReceipt.find(
        binding_id=(await WhatsAppInboundReceipt.find())[0].binding_id,
        meta_message_id="wamid.repeat",
    )
    assert len(receipts) == 1
    _assert_no_authorization(client)


@pytest.mark.asyncio
async def test_non_text_inbound_stores_receipt_and_sends_text_only_notice(
    enc_key, test_user, client, sent_texts
):
    """Non-text inbound stores a receipt and sends the text-only notice."""
    from app.models.whatsapp_inbound import WhatsAppInboundReceipt

    binding = await _ready_binding(test_user)
    raw = _raw_payload(
        _envelope(
            [
                {
                    "from": "15551212",
                    "id": "wamid.image",
                    "type": "image",
                    "image": {"id": "media-1"},
                }
            ],
            phone_number_id=_PHONE,
        )
    )
    signature = _jvconnect_signature(raw, _HOOK_SECRET)
    response = await _post_webhook(client, raw, signature, header="X-Hub-Signature-256")

    assert response.status_code == 200
    assert sent_texts == [
        {
            "base_url": "https://connect.example",
            "to": "15551212",
            "body": "Text only for now",
            "client": None,
        }
    ]
    assert [call["message_id"] for call in sent_texts.typing] == ["wamid.image"]
    receipts = await WhatsAppInboundReceipt.find(
        binding_id=binding.id, meta_message_id="wamid.image"
    )
    assert len(receipts) == 1
    assert receipts[0].kind == "non_text"
    _assert_no_chat_submit()


@pytest.mark.asyncio
async def test_unknown_sender_sends_link_notice_and_does_not_submit_chat(
    enc_key, test_user, client, sent_texts
):
    """An unknown sender gets the link notice and no other send."""
    await _ready_binding(test_user)
    raw = _raw_payload(
        _envelope(
            [_text_message("hello", sender="15559999", message_id="wamid.unknown")],
            phone_number_id=_PHONE,
        )
    )
    signature = _jvconnect_signature(raw, _HOOK_SECRET)
    response = await _post_webhook(
        client, raw, signature, header="X-Jvconnect-Signature"
    )

    assert response.status_code == 200
    assert [call["body"] for call in sent_texts] == [
        "Link your number in Integral settings"
    ]
    assert sent_texts.typing == [
        {
            "base_url": "https://connect.example",
            "message_id": "wamid.unknown",
            "client": None,
        }
    ]
    _assert_no_chat_submit()


@pytest.mark.asyncio
async def test_verified_text_is_accepted_without_a_reply(
    enc_key, test_user, client, sent_texts, monkeypatch
):
    """A verified sender who is not a member is refused and does not chat."""
    from app.models.whatsapp_inbound import WhatsAppInboundReceipt

    binding = await _ready_binding(test_user)
    seen = []

    async def resolve(channel, channel_user_id):
        seen.append((channel, channel_user_id))
        if channel == "whatsapp" and channel_user_id == "+15551212":
            return {"user_id": "user-verified"}
        return None

    monkeypatch.setattr(
        "app.agentive.services.channel_identity.resolve_channel_identity",
        resolve,
    )
    raw = _raw_payload(
        _envelope(
            [_text_message("hello", sender="15551212", message_id="wamid.verified")],
            phone_number_id=_PHONE,
        )
    )
    signature = _jvconnect_signature(raw, _HOOK_SECRET)
    response = await _post_webhook(
        client, raw, signature, header="X-Jvconnect-Signature"
    )

    assert response.status_code == 200
    assert [call["body"] for call in sent_texts] == [
        "This number isn't a member of a workspace on this WhatsApp line."
    ]
    assert [call["message_id"] for call in sent_texts.typing] == ["wamid.verified"]
    receipts = await WhatsAppInboundReceipt.find(
        binding_id=binding.id, meta_message_id="wamid.verified"
    )
    assert len(receipts) == 1
    assert receipts[0].status == "notice_sent"
    assert receipts[0].kind == "text"
    assert ("whatsapp", "+15551212") in seen
    _assert_no_chat_submit()


@pytest.mark.asyncio
async def test_owner_text_shows_typing_before_the_turn(
    enc_key, test_user, client, sent_texts, monkeypatch
):
    """A linked sender sees typing before the chat turn starts."""
    binding = await _ready_binding(test_user)
    order = []

    async def resolve(channel, channel_user_id):
        if channel == "whatsapp" and channel_user_id == "+15551212":
            return {
                "user_id": binding.user_id,
                "channel_user_id": "+15551212",
            }
        return None

    async def admit(bound, envelope, *, e164, user_id="", workspace_id=""):
        order.append(("admit", envelope.message_id, e164))

    async def typing(base_url, api_key, message_id, *, client=None):
        order.append(("typing", message_id))
        sent_texts.typing.append({"message_id": message_id})
        from app.agentive.clients.jvconnect_whatsapp import JvconnectResult

        return JvconnectResult(ok=True, status_code=200, data={}, error="")

    monkeypatch.setattr(
        "app.agentive.services.channel_identity.resolve_channel_identity",
        resolve,
    )
    monkeypatch.setattr(
        "app.agentive.services.whatsapp_chat_bridge.admit_owner_whatsapp_text",
        admit,
    )
    monkeypatch.setattr(
        "app.agentive.clients.jvconnect_whatsapp.send_typing_indicator",
        typing,
    )
    raw = _raw_payload(
        _envelope(
            [_text_message("hello", sender="15551212", message_id="wamid.owner")],
            phone_number_id=_PHONE,
        )
    )
    signature = _jvconnect_signature(raw, _HOOK_SECRET)
    response = await _post_webhook(
        client, raw, signature, header="X-Jvconnect-Signature"
    )

    assert response.status_code == 200
    assert order == [("typing", "wamid.owner"), ("admit", "wamid.owner", "+15551212")]
    assert sent_texts == []


@pytest.mark.asyncio
async def test_typing_failure_still_sends_the_reply(
    enc_key, test_user, client, sent_texts, monkeypatch
):
    """A failed typing indicator does not block the notice."""

    async def fail_typing(*args: object, **kwargs: object) -> None:
        """Typing indicator is unavailable."""
        raise RuntimeError("typing down")

    monkeypatch.setattr(
        "app.agentive.clients.jvconnect_whatsapp.send_typing_indicator",
        fail_typing,
    )
    await _ready_binding(test_user)
    raw = _raw_payload(
        _envelope(
            [_text_message("hello", sender="15559999", message_id="wamid.typing")],
            phone_number_id=_PHONE,
        )
    )
    signature = _jvconnect_signature(raw, _HOOK_SECRET)
    response = await _post_webhook(
        client, raw, signature, header="X-Jvconnect-Signature"
    )

    assert response.status_code == 200
    assert [call["body"] for call in sent_texts] == [
        "Link your number in Integral settings"
    ]


@pytest.mark.asyncio
async def test_disabled_binding_returns_200_and_does_not_send(
    enc_key, test_user, client, sent_texts
):
    """A disabled binding accepts the webhook and sends no notice."""
    await _ready_binding(test_user, enabled=False)
    raw = _raw_payload(
        _envelope(
            [_text_message("hello", sender="15551212", message_id="wamid.disabled")],
            phone_number_id=_PHONE,
        )
    )
    signature = _jvconnect_signature(raw, _HOOK_SECRET)
    first = await _post_webhook(client, raw, signature, header="X-Jvconnect-Signature")
    second = await _post_webhook(client, raw, signature, header="X-Jvconnect-Signature")

    assert first.status_code == 200
    assert second.status_code == 200
    assert sent_texts == []
    _assert_no_chat_submit()


@pytest.mark.asyncio
async def test_pending_inbound_verifies_and_blocks_a_second_account(
    enc_key, test_user, second_user, client, sent_texts, monkeypatch
):
    """A matching inbound verifies the claim and does not start a turn."""
    from app.agentive.services.channel_identity import initiate_link

    admitted = []

    async def admit(*args: object, **kwargs: object) -> None:
        admitted.append(args)

    monkeypatch.setattr(
        "app.agentive.services.whatsapp_chat_bridge.admit_owner_whatsapp_text",
        admit,
    )
    await _ready_binding(test_user)
    started = await initiate_link(_user_id(test_user), "whatsapp", "+15551212")
    assert "otp_code" not in started
    assert started["verified"] is False
    rejected = await initiate_link(_user_id(test_user), "whatsapp", "5556767191")
    assert "country code" in rejected["error"]
    raw = _raw_payload(
        _envelope(
            [_text_message("hello", sender="15551212", message_id="wamid.link")],
            phone_number_id=_PHONE,
        )
    )
    signature = _jvconnect_signature(raw, _HOOK_SECRET)
    response = await _post_webhook(client, raw, signature, header="X-Jvconnect-Signature")
    assert response.status_code == 200
    assert [call["body"] for call in sent_texts] == [
        "Number linked. Send a message to continue."
    ]
    assert admitted == []
    other = await initiate_link(_user_id(second_user), "whatsapp", "+15551212")
    assert "another account" in other["error"]


@pytest.mark.asyncio
async def test_two_workspaces_ask_and_then_use_the_choice(
    enc_key, test_user, client, sent_texts, monkeypatch
):
    """Several workspaces ask once, then later messages use that choice."""
    from app.agentive.services.channel_identity import initiate_link
    from app.agentive.services.whatsapp_channel_binding import grant_whatsapp_line
    from app.models.edges import IS_MEMBER_OF
    from app.models.nodes import Workspace
    from app.services.app_graph import catalog_workspace
    from app.services.permissions import get_user_node
    from app.utils.time import utc_now_iso

    admitted = []

    async def admit(binding, envelope, *, e164, user_id, workspace_id):
        admitted.append(workspace_id)

    monkeypatch.setattr(
        "app.agentive.services.whatsapp_chat_bridge.admit_owner_whatsapp_text",
        admit,
    )
    binding = await _ready_binding(test_user)
    await initiate_link(_user_id(test_user), "whatsapp", "+15551212")
    link_raw = _raw_payload(
        _envelope(
            [_text_message("hello", sender="15551212", message_id="wamid.claim")],
            phone_number_id=_PHONE,
        )
    )
    await _post_webhook(
        client,
        link_raw,
        _jvconnect_signature(link_raw, _HOOK_SECRET),
        header="X-Jvconnect-Signature",
    )
    user = await get_user_node(_user_id(test_user))
    now = utc_now_iso()
    org = await Workspace.create(
        kind="organization",
        name="Alpha Org",
        name_fold="alpha org",
        created_at=now,
        updated_at=now,
    )
    await catalog_workspace(org)
    await user.connect(org, edge=IS_MEMBER_OF, role="admin", joined_at=now)
    await grant_whatsapp_line(org.id, binding, _user_id(test_user))

    async def post(body: str, message_id: str):
        raw = _raw_payload(
            _envelope(
                [_text_message(body, sender="15551212", message_id=message_id)],
                phone_number_id=_PHONE,
            )
        )
        return await _post_webhook(
            client,
            raw,
            _jvconnect_signature(raw, _HOOK_SECRET),
            header="X-Jvconnect-Signature",
        )

    asked = await post("hello", "wamid.ask")
    assert asked.status_code == 200
    assert any("Which workspace?" in call["body"] for call in sent_texts)
    assert admitted == []
    chosen = await post("1", "wamid.pick")
    assert chosen.status_code == 200
    assert any(call["body"].startswith("Using ") for call in sent_texts)
    chatted = await post("status", "wamid.chat")
    assert chatted.status_code == 200
    personal = await Workspace.get(binding.workspace_id)
    ordered = sorted(
        [
            (getattr(personal, "name", "") or "", personal.id),
            (org.name, org.id),
        ],
        key=lambda item: (item[0].casefold(), item[1]),
    )
    assert admitted == [ordered[0][1]]


@pytest.mark.asyncio
async def test_removed_member_is_refused(
    enc_key, test_user, client, sent_texts, monkeypatch
):
    """A sender who lost workspace access does not start a turn."""
    from app.models.edges import IS_MEMBER_OF
    from app.services.permissions import get_user_node

    admitted = []

    async def admit(*args: object, **kwargs: object) -> None:
        admitted.append(args)

    async def resolve(channel, channel_user_id):
        if channel_user_id == "+15551212":
            return {"user_id": _user_id(test_user), "channel_user_id": "+15551212"}
        return None

    monkeypatch.setattr(
        "app.agentive.services.channel_identity.resolve_channel_identity",
        resolve,
    )
    monkeypatch.setattr(
        "app.agentive.services.whatsapp_chat_bridge.admit_owner_whatsapp_text",
        admit,
    )
    binding = await _ready_binding(test_user)
    user = await get_user_node(_user_id(test_user))
    context = await user.get_context()
    edges = await context.find_edges_between(
        user.id, binding.workspace_id, edge_class=IS_MEMBER_OF
    )
    for edge in edges:
        await edge.delete()
    raw = _raw_payload(
        _envelope(
            [_text_message("hello", sender="15551212", message_id="wamid.removed")],
            phone_number_id=_PHONE,
        )
    )
    response = await _post_webhook(
        client,
        raw,
        _jvconnect_signature(raw, _HOOK_SECRET),
        header="X-Jvconnect-Signature",
    )
    assert response.status_code == 200
    assert [call["body"] for call in sent_texts] == [
        "This number isn't a member of a workspace on this WhatsApp line."
    ]
    assert admitted == []
