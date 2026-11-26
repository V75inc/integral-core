"""Unit tests for WhatsApp call-control. No network and no database."""

from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from app.agentive.services.whatsapp_call_control import (
    ParsedWhatsAppCall,
    _voice_service,
    accept_jvvoice_call,
    disconnect_jvvoice_call,
    handle_whatsapp_call,
    parse_whatsapp_call,
)
from app.agentive.services.whatsapp_ingress import (
    parse_whatsapp_envelope,
    receive_whatsapp_webhook,
)
from app.api.errors import MissingAuthenticationError
from app.config import Settings, settings
from app.schemas.agentive.whatsapp_call import JvvoiceAcceptCallRequest


def _calls_payload(
    call: dict | None = None,
    *,
    statuses: list | None = None,
    field: str = "calls",
    phone_number_id: str = "pn-1",
    leading_change: dict | None = None,
) -> dict:
    value: dict = {"metadata": {"phone_number_id": phone_number_id}}
    if call is not None:
        value["calls"] = [call]
    if statuses is not None:
        value["statuses"] = statuses
    changes = []
    if leading_change is not None:
        changes.append(leading_change)
    changes.append({"field": field, "value": value})
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": changes}],
    }


def _offer_call(**overrides) -> dict:
    call = {
        "id": "wacid.1",
        "from": "15551212000",
        "event": "connect",
        "session": {"sdp": "v=0\r\no=- 1 1 IN IP4 127.0.0.1"},
    }
    call.update(overrides)
    return call


@pytest.mark.unit
def test_call_with_id_caller_and_sdp_is_actionable():
    """id, from, and session.sdp make a call actionable, with sdp_type offer."""
    payload = _calls_payload(_offer_call())
    parsed = parse_whatsapp_call(payload)

    assert isinstance(parsed, ParsedWhatsAppCall)
    assert parsed.actionable is True
    assert parsed.call_id == "wacid.1"
    assert parsed.caller == "15551212000"
    assert parsed.sdp.startswith("v=0")
    assert parsed.sdp_type == "offer"
    assert parsed.phone_number_id == "pn-1"
    assert parsed.event == "connect"


@pytest.mark.unit
def test_sdp_keeps_trailing_newlines():
    """SDP trailing CRLF stays. Stripping it makes LiveKit reject the offer."""
    payload = _calls_payload(
        _offer_call(session={"sdp": "v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\n\r\n"})
    )
    parsed = parse_whatsapp_call(payload)

    assert parsed.actionable is True
    assert parsed.sdp.endswith("\r\n")
    assert parsed.sdp.startswith("v=0")


@pytest.mark.unit
def test_explicit_sdp_type_is_kept():
    """A provided sdp_type is kept instead of the offer default."""
    payload = _calls_payload(_offer_call(session={"sdp": "v=0", "sdp_type": "answer"}))
    parsed = parse_whatsapp_call(payload)

    assert parsed.actionable is True
    assert parsed.sdp_type == "answer"


@pytest.mark.unit
def test_calls_change_after_a_message_change_is_still_read():
    """The parser uses the first field=calls change, not the first change."""
    payload = _calls_payload(
        _offer_call(),
        leading_change={
            "field": "messages",
            "value": {"messages": [{"from": "1", "id": "m", "type": "text"}]},
        },
    )
    parsed = parse_whatsapp_call(payload)

    assert parsed.actionable is True
    assert parsed.call_id == "wacid.1"


@pytest.mark.unit
def test_call_without_sdp_is_not_actionable_or_non_text():
    """An event with no SDP is not actionable and is not non_text."""
    payload = _calls_payload(
        {"id": "wacid.1", "from": "15551212000", "event": "terminate"}
    )
    parsed = parse_whatsapp_call(payload)

    assert parsed.actionable is False
    assert getattr(parsed, "kind", None) != "non_text"
    assert parse_whatsapp_envelope(payload).kind == "ignore"


@pytest.mark.unit
def test_status_only_calls_payload_is_not_actionable_or_non_text():
    """A calls field that only carries statuses is not a media offer."""
    payload = _calls_payload(
        None,
        statuses=[{"id": "wacid.1", "status": "RINGING"}],
    )
    parsed = parse_whatsapp_call(payload)

    assert parsed.actionable is False
    assert getattr(parsed, "kind", None) != "non_text"
    assert parse_whatsapp_envelope(payload).kind == "ignore"


@pytest.mark.unit
def test_unknown_event_with_sdp_is_actionable():
    """Event names are not a whitelist. SDP is what makes a call actionable."""
    payload = _calls_payload(_offer_call(event="not-a-meta-event"))
    parsed = parse_whatsapp_call(payload)

    assert parsed.actionable is True
    assert parsed.event == "not-a-meta-event"


@pytest.mark.unit
@pytest.mark.parametrize(
    "call",
    [
        {"id": "", "from": "15551212000", "session": {"sdp": "v=0"}},
        {"id": "wacid.1", "from": "", "session": {"sdp": "v=0"}},
        {"id": "wacid.1", "from": "15551212000", "session": {"sdp": "  "}},
        {"id": "wacid.1", "from": "15551212000"},
    ],
)
def test_missing_id_caller_or_sdp_is_not_actionable(call: dict):
    """A blank id, caller, or SDP cannot be accepted."""
    parsed = parse_whatsapp_call(_calls_payload(call))
    assert parsed.actionable is False
    assert getattr(parsed, "kind", None) != "non_text"


@pytest.mark.unit
def test_parser_does_not_admit_or_perform_http():
    """Admission and HTTP stay out of the pure parser."""
    source = inspect.getsource(parse_whatsapp_call)
    assert "admit_whatsapp_caller" not in source
    assert "httpx" not in source
    assert "requests" not in source
    assert "urllib" not in source


@pytest.mark.unit
def test_jvvoice_settings_default_empty_and_are_not_livekit_keys():
    """JVVOICE_BASE_URL and JVVOICE_API_KEY default empty and are not LiveKit."""
    assert Settings.model_fields["JVVOICE_BASE_URL"].default == ""
    assert Settings.model_fields["JVVOICE_API_KEY"].default == ""
    example = (Path(__file__).resolve().parents[2] / ".env.example").read_text(
        encoding="utf-8"
    )
    assert "JVVOICE_BASE_URL=" in example
    assert "JVVOICE_API_KEY=" in example
    assert "LIVEKIT_URL" not in example
    assert "LIVEKIT_API_KEY" not in example
    window = example.split("JVVOICE_BASE_URL=", 1)[0].rsplit("\n", 4)[-4:]
    comment = "\n".join(window).lower()
    assert "jvvoice" in comment
    assert "livekit" in comment


_JVVOICE = "https://voice.example.test"
_JVVOICE_KEY = "jvv_do_not_log"
_META_TOKEN = "EAAG_do_not_log"
_SDP = "v=0\r\no=- 1 1 IN IP4 127.0.0.1"


def _jvvoice_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _logged(caplog) -> str:
    parts = []
    for record in caplog.records:
        parts.append(record.getMessage())
        if record.args:
            parts.append(repr(record.args))
        if record.exc_text:
            parts.append(record.exc_text)
    return "\n".join(parts)


@pytest.fixture
def jvvoice_settings(monkeypatch):
    """Point call-control at the mocked jvvoice base URL and server key."""
    monkeypatch.setattr(settings, "JVVOICE_BASE_URL", _JVVOICE)
    monkeypatch.setattr(settings, "JVVOICE_API_KEY", _JVVOICE_KEY)
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", "https://api.example.test")


@pytest.fixture(autouse=True)
def _reset_process_call_claims():
    """Keep one test's call claim from blocking the next accept."""
    from app.agentive.api import native_voice as endpoint
    from app.agentive.services.whatsapp_call_control import clear_process_call_claims

    clear_process_call_claims()
    saved = set(endpoint._active_calls)
    endpoint._active_calls.clear()
    yield
    clear_process_call_claims()
    endpoint._active_calls.clear()
    endpoint._active_calls.update(saved)


@pytest.mark.unit
def test_incomplete_line_voice_service_does_not_use_deployment_defaults(
    jvvoice_settings,
):
    """A saved URL without a usable key does not call the deployment host."""
    url_only = SimpleNamespace(
        id="binding-1",
        jvvoice_base_url="https://line.example.test",
        jvvoice_api_key_ciphertext="",
    )
    broken = SimpleNamespace(
        id="binding-1",
        jvvoice_base_url="https://line.example.test",
        jvvoice_api_key_ciphertext="v1:not-valid",
    )

    assert _voice_service(url_only) == ("", "")
    assert _voice_service(broken) == ("", "")
    assert _voice_service(None) == (_JVVOICE, _JVVOICE_KEY)


@pytest.mark.unit
def test_accept_schema_rejects_jvagent_fields():
    """The accept body has no place for jvagent ids."""
    with pytest.raises(ValidationError):
        JvvoiceAcceptCallRequest(
            whatsapp_call_id="wacid.1",
            phone_number_id="pn-1",
            whatsapp_api_key=_META_TOKEN,
            sdp=_SDP,
            sdp_type="offer",
            caller_phone="+15551212000",
            jvagent_agent_id="agent-1",
            jvagent_base_url="https://agent.example.test",
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_accept_posts_media_fields_and_jvvoice_bearer(jvvoice_settings):
    """Accept posts the media fields and omits both jvagent ids."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["auth"] = request.headers["Authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"status": "connected", "call_id": "wacid.1"})

    async with _jvvoice_client(handler) as client:
        result = await accept_jvvoice_call(
            whatsapp_call_id="wacid.1",
            phone_number_id="pn-1",
            whatsapp_api_key=_META_TOKEN,
            sdp=_SDP,
            sdp_type="offer",
            caller_phone="+15551212000",
            client=client,
        )

    assert result.ok is True
    assert captured["method"] == "POST"
    assert captured["url"] == f"{_JVVOICE}/api/calls/accept"
    assert captured["auth"] == f"Bearer {_JVVOICE_KEY}"
    assert captured["body"] == {
        "whatsapp_call_id": "wacid.1",
        "phone_number_id": "pn-1",
        "whatsapp_api_key": _META_TOKEN,
        "sdp": _SDP,
        "sdp_type": "offer",
        "caller_phone": "+15551212000",
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_accept_logs_omit_jvvoice_key_and_meta_token(jvvoice_settings, caplog):
    """Successful and failed accept logs omit the bearer, token, and SDP."""
    caplog.set_level(logging.DEBUG)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"status": "connected"})
        return httpx.Response(500, content=_META_TOKEN.encode("utf-8"))

    async with _jvvoice_client(handler) as client:
        ok = await accept_jvvoice_call(
            whatsapp_call_id="wacid.1",
            phone_number_id="pn-1",
            whatsapp_api_key=_META_TOKEN,
            sdp=_SDP,
            sdp_type="offer",
            caller_phone="+15551212000",
            client=client,
        )
        failed = await accept_jvvoice_call(
            whatsapp_call_id="wacid.1",
            phone_number_id="pn-1",
            whatsapp_api_key=_META_TOKEN,
            sdp=_SDP,
            sdp_type="offer",
            caller_phone="+15551212000",
            client=client,
        )

    assert ok.ok is True
    assert failed.ok is False
    logged = _logged(caplog)
    assert logged
    assert _JVVOICE_KEY not in logged
    assert _META_TOKEN not in logged
    assert _SDP not in logged
    assert "Bearer" not in logged


@pytest.mark.unit
@pytest.mark.asyncio
async def test_accept_logs_422_loc_and_msg_without_secrets(jvvoice_settings, caplog):
    """A 422 logs FastAPI loc/msg and never the request input, token, or SDP."""
    caplog.set_level(logging.DEBUG)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "detail": [
                    {
                        "type": "missing",
                        "loc": ["body", "jvagent_agent_id"],
                        "msg": "Field required",
                        "input": {
                            "whatsapp_api_key": _META_TOKEN,
                            "sdp": _SDP,
                            "relay_bearer": _JVVOICE_KEY,
                        },
                    }
                ]
            },
        )

    async with _jvvoice_client(handler) as client:
        result = await accept_jvvoice_call(
            whatsapp_call_id="wacid.1",
            phone_number_id="pn-1",
            whatsapp_api_key=_META_TOKEN,
            sdp=_SDP,
            sdp_type="offer",
            caller_phone="+15551212000",
            bridge="integral_native",
            relay_url="wss://api.example.test/ws/phone-voice",
            relay_bearer="relay-bearer",
            session_id="thread-1",
            user_id="user-1",
            client=client,
        )

    assert result.ok is False
    assert result.status_code == 422
    logged = _logged(caplog)
    assert "body.jvagent_agent_id:Field required" in logged
    assert _JVVOICE_KEY not in logged
    assert _META_TOKEN not in logged
    assert _SDP not in logged
    assert "relay-bearer" not in logged


@pytest.mark.unit
@pytest.mark.asyncio
async def test_accept_logs_500_error_without_secrets(jvvoice_settings, caplog):
    """A 500 logs the jvvoice error string and never the token or SDP."""
    caplog.set_level(logging.DEBUG)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={
                "status": "error",
                "error": "invalid SDP provided: failed to unmarshal SDP: EOF",
                "sdp": _SDP,
                "whatsapp_api_key": _META_TOKEN,
            },
        )

    async with _jvvoice_client(handler) as client:
        result = await accept_jvvoice_call(
            whatsapp_call_id="wacid.1",
            phone_number_id="pn-1",
            whatsapp_api_key=_META_TOKEN,
            sdp=_SDP,
            sdp_type="offer",
            caller_phone="+15551212000",
            client=client,
        )

    assert result.ok is False
    assert result.status_code == 500
    logged = _logged(caplog)
    assert "unmarshal SDP: EOF" in logged
    assert _META_TOKEN not in logged
    assert _SDP not in logged
    assert _JVVOICE_KEY not in logged


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_connected_status_is_not_success_and_is_not_retried(
    jvvoice_settings,
):
    """A body whose status is not connected fails once, with no second post."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"status": "error", "call_id": "wacid.1"})

    async with _jvvoice_client(handler) as client:
        result = await accept_jvvoice_call(
            whatsapp_call_id="wacid.1",
            phone_number_id="pn-1",
            whatsapp_api_key=_META_TOKEN,
            sdp=_SDP,
            sdp_type="offer",
            caller_phone="+15551212000",
            client=client,
        )

    assert result.ok is False
    assert calls["n"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_disconnect_error_returns_status_and_does_not_accept(jvvoice_settings):
    """Disconnect posts user_initiated false and a failure does not accept."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        body = json.loads(request.content)
        assert body["user_initiated"] is False
        return httpx.Response(502, json={"status": "error"})

    async with _jvvoice_client(handler) as client:
        result = await disconnect_jvvoice_call(
            whatsapp_call_id="wacid.1",
            client=client,
        )

    assert result.ok is False
    assert result.status_code == 502
    assert seen == [f"{_JVVOICE}/api/calls/disconnect"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_accept_checks_outbound_url_before_sending(monkeypatch):
    """The owned client path checks the jvvoice base URL and does not send."""
    seen: dict[str, str] = {}

    async def fake_validate(url: str) -> None:
        seen["url"] = url
        from app.exceptions import BadRequestError

        raise BadRequestError(message="host not allowed")

    monkeypatch.setattr(
        "app.agentive.services.whatsapp_call_control.validate_outbound_http_url",
        fake_validate,
    )
    monkeypatch.setattr(settings, "JVVOICE_BASE_URL", _JVVOICE)
    monkeypatch.setattr(settings, "JVVOICE_API_KEY", _JVVOICE_KEY)

    result = await accept_jvvoice_call(
        whatsapp_call_id="wacid.1",
        phone_number_id="pn-1",
        whatsapp_api_key=_META_TOKEN,
        sdp=_SDP,
        sdp_type="offer",
        caller_phone="+15551212000",
    )

    assert seen["url"] == f"{_JVVOICE}/api/calls/accept"
    assert result.ok is False
    assert result.status_code == 0


_HOOK_SECRET = "whsec_call_test"
_JVCONNECT_KEY = "jvk_call_test"
_HELD_TOKEN = "EAAG_held_token"


class _MemoryReceipts:
    def __init__(self) -> None:
        self.rows: list[SimpleNamespace] = []

    async def find(self, binding_id: str, meta_message_id: str):
        return [
            row
            for row in self.rows
            if row.binding_id == binding_id and row.meta_message_id == meta_message_id
        ]

    async def create(self, **fields):
        row = SimpleNamespace(**fields)
        self.rows.append(row)
        return row


def _hook_signature(raw: bytes, secret: str = _HOOK_SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return "sha256=" + digest


def _call_binding() -> SimpleNamespace:
    return SimpleNamespace(
        id="binding-1",
        user_id="user-1",
        workspace_id="",
        jvconnect_base_url="https://connect.example.test",
        phone_number_id="pn-1",
        display_phone="+15550009999",
        enabled=True,
        last_error="",
        api_key_last4="test",
        webhook_secret_ciphertext="stored",
    )


def _owner_identity() -> SimpleNamespace:
    return SimpleNamespace(
        user_id="user-1",
        channel_user_id="+15551212000",
        verified=True,
    )


def _enabled_grant(**overrides) -> SimpleNamespace:
    fields = {"workspace_id": "ws-1", "enabled": True, "calling_enabled": True}
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _actionable_call_payload() -> dict:
    return _calls_payload(_offer_call())


def _raw(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _install_webhook_fakes(monkeypatch, binding, identities, grants):
    import app.agentive.services.whatsapp_ingress as ingress

    async def find_bindings(_phone_number_id: str):
        return [binding]

    async def load_call_inputs(_binding):
        return list(identities), list(grants)

    dispatched = {"called": False}

    async def dispatch(_binding, envelope):
        dispatched["called"] = True
        dispatched["kind"] = envelope.kind
        return {"ok": True}

    monkeypatch.setattr(ingress, "read_webhook_secret", lambda _binding: _HOOK_SECRET)
    monkeypatch.setattr(ingress, "read_api_key", lambda _binding: _JVCONNECT_KEY)
    monkeypatch.setattr(ingress, "_dispatch_verified", dispatch)

    async def dedicated(user_id, workspace_id, e164):
        from app.agentive.services.whatsapp_chat_bridge import _session_id

        return SimpleNamespace(
            id="n.ChatThread.call",
            provider_session_id=_session_id(workspace_id, e164),
            user_id=user_id,
            workspace_id=workspace_id,
        )

    # The accept path binds the existing WhatsApp thread. These tests have
    # no chat provider and no graph, so the lookup stays a stub.
    monkeypatch.setattr(
        "app.agentive.services.whatsapp_chat_bridge._dedicated_thread",
        dedicated,
    )
    return find_bindings, load_call_inputs, dispatched


async def _receive(
    monkeypatch,
    payload: dict,
    *,
    identities,
    grants,
    handler,
    signature: str | None = None,
    binding=None,
):
    binding = binding or _call_binding()
    find_bindings, load_call_inputs, dispatched = _install_webhook_fakes(
        monkeypatch, binding, identities, grants
    )
    receipts = _MemoryReceipts()
    raw = _raw(payload)
    signed = _hook_signature(raw) if signature is None else signature
    seen: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    async with _jvvoice_client(transport) as client:
        result = await receive_whatsapp_webhook(
            raw,
            signed,
            find_bindings=find_bindings,
            load_call_inputs=load_call_inputs,
            http_client=client,
            receipt_store=receipts,
        )
    return result, seen, receipts, binding, dispatched


def _paths(seen: list[httpx.Request]) -> list[str]:
    return [str(request.url) for request in seen]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("signature", ["", "sha256=" + ("ab" * 32)])
async def test_forged_or_missing_signature_makes_no_provider_request(
    monkeypatch, signature
):
    """A bad signature raises and does not call jvconnect or jvvoice."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(500, json={"error": "should not be called"})

    binding = _call_binding()
    find_bindings, _load, _dispatched = _install_webhook_fakes(
        monkeypatch, binding, [_owner_identity()], [_enabled_grant()]
    )
    raw = _raw(_actionable_call_payload())
    async with _jvvoice_client(handler) as client:
        with pytest.raises(MissingAuthenticationError):
            await receive_whatsapp_webhook(
                raw,
                signature,
                find_bindings=find_bindings,
                load_call_inputs=None,
                http_client=client,
                receipt_store=_MemoryReceipts(),
            )
    assert seen == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_signed_actionable_call_accepts_once_and_skips_text_dispatch(
    monkeypatch, jvvoice_settings, caplog
):
    """An admitted call fetches one token and posts one accept."""
    caplog.set_level(logging.DEBUG)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/calling/credentials"):
            assert request.headers["Authorization"] == f"Bearer {_JVCONNECT_KEY}"
            return httpx.Response(
                200,
                json={"access_token": _META_TOKEN, "phone_number_id": "pn-1"},
            )
        body = json.loads(request.content)
        assert body["whatsapp_api_key"] == _META_TOKEN
        assert "jvagent_agent_id" not in body
        assert "jvagent_base_url" not in body
        return httpx.Response(200, json={"status": "connected"})

    result, seen, receipts, binding, dispatched = await _receive(
        monkeypatch,
        _actionable_call_payload(),
        identities=[_owner_identity()],
        grants=[_enabled_grant()],
        handler=handler,
    )

    assert result == {"ok": True}
    assert dispatched["called"] is False
    assert _paths(seen) == [
        "https://connect.example.test/api/v1/meta/whatsapp/calling/credentials",
        f"{_JVVOICE}/api/calls/accept",
    ]
    assert len(receipts.rows) == 1
    assert receipts.rows[0].kind == "call"
    assert receipts.rows[0].meta_message_id == "wacid.1"
    assert receipts.rows[0].status == "accepted"
    from app.agentive.services.whatsapp_channel_binding import public_binding_view

    view = public_binding_view(binding)
    logged = _logged(caplog)
    blob = logged + "\n" + json.dumps(view)
    assert _META_TOKEN not in blob
    assert _JVVOICE_KEY not in blob
    assert "access_token" not in binding.__dict__


@pytest.mark.unit
@pytest.mark.asyncio
async def test_signed_text_still_uses_the_text_dispatcher(monkeypatch):
    """A text envelope does not enter call handling."""
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": "pn-1"},
                            "messages": [
                                {
                                    "from": "15551212000",
                                    "id": "wamid.1",
                                    "type": "text",
                                    "text": {"body": "hello"},
                                }
                            ],
                        },
                    }
                ]
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"text path made a call request: {request.url}")

    result, seen, receipts, _binding, dispatched = await _receive(
        monkeypatch,
        payload,
        identities=[_owner_identity()],
        grants=[_enabled_grant()],
        handler=handler,
    )

    assert result == {"ok": True}
    assert dispatched["called"] is True
    assert dispatched["kind"] == "text"
    assert seen == []
    assert receipts.rows == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_visitor_call_does_not_enter_native_voice(monkeypatch, jvvoice_settings):
    """A ready public profile refuses the call instead of opening native voice."""

    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("visitor call entered native voice")

    monkeypatch.setattr(
        "app.agentive.services.whatsapp_voice_bridge.start_phone_relay", boom
    )
    monkeypatch.setattr("app.services.native_voice.authorize", boom)
    monkeypatch.setattr(
        "app.agentive.services.approval_decisions.decide_staged_write", boom
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"visitor call contacted {request.url}")

    _result, seen, receipts, _binding, dispatched = await _receive(
        monkeypatch,
        _calls_payload(_offer_call(**{"from": "19995550000"})),
        identities=[_owner_identity()],
        grants=[_enabled_grant(public_profile_ready=True)],
        handler=handler,
    )

    assert seen == []
    assert dispatched["called"] is False
    assert receipts.rows[0].status == "refused"
    assert receipts.rows[0].last_error == "visitor_voice_unavailable"
    assert "ws-" not in receipts.rows[0].last_error


@pytest.mark.unit
@pytest.mark.asyncio
async def test_owner_call_still_accepts_when_a_public_profile_is_ready(
    monkeypatch, jvvoice_settings
):
    """A matching owner stays on the member relay when a public profile is set."""
    accepts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/calling/credentials"):
            return httpx.Response(200, json={"access_token": _META_TOKEN})
        accepts["n"] += 1
        return httpx.Response(200, json={"status": "connected"})

    _result, _seen, receipts, _binding, _dispatched = await _receive(
        monkeypatch,
        _actionable_call_payload(),
        identities=[_owner_identity()],
        grants=[_enabled_grant(public_profile_ready=True)],
        handler=handler,
    )

    assert accepts["n"] == 1
    assert receipts.rows[0].status == "accepted"
    assert receipts.rows[0].last_error != "visitor_voice_unavailable"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_refused_caller_does_not_accept(monkeypatch, jvvoice_settings):
    """A mismatch records refused and does not fetch or accept."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"refused caller contacted {request.url}")

    _result, seen, receipts, _binding, dispatched = await _receive(
        monkeypatch,
        _calls_payload(_offer_call(**{"from": "19995550000"})),
        identities=[_owner_identity()],
        grants=[_enabled_grant()],
        handler=handler,
    )

    assert seen == []
    assert dispatched["called"] is False
    assert receipts.rows[0].kind == "call"
    assert receipts.rows[0].status == "refused"
    assert all(row.kind != "non_text" for row in receipts.rows)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_calling_enabled_attribute_refuses(monkeypatch, jvvoice_settings):
    """A grant without calling_enabled is treated as false."""
    grant = SimpleNamespace(workspace_id="ws-1", enabled=True)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(request.url)

    _result, seen, receipts, _binding, _dispatched = await _receive(
        monkeypatch,
        _actionable_call_payload(),
        identities=[_owner_identity()],
        grants=[grant],
        handler=handler,
    )

    assert seen == []
    assert receipts.rows[0].status == "refused"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_call_for_another_line_is_refused(monkeypatch, jvvoice_settings):
    """The payload phone number must be the bound line."""
    binding = _call_binding()
    binding.phone_number_id = "pn-other"

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(request.url)

    _result, seen, receipts, _binding, _dispatched = await _receive(
        monkeypatch,
        _actionable_call_payload(),
        identities=[_owner_identity()],
        grants=[_enabled_grant()],
        handler=handler,
        binding=binding,
    )

    assert seen == []
    assert receipts.rows[0].status == "refused"
    assert receipts.rows[0].last_error == "line_mismatch"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_answer_without_a_principal_is_refused(monkeypatch, jvvoice_settings):
    """An identity with no user id is not accepted onto the phone relay."""
    identity = _owner_identity()
    identity.user_id = ""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(request.url)

    _result, seen, receipts, _binding, _dispatched = await _receive(
        monkeypatch,
        _actionable_call_payload(),
        identities=[identity],
        grants=[_enabled_grant()],
        handler=handler,
    )

    assert seen == []
    assert receipts.rows[0].status == "refused"
    assert receipts.rows[0].last_error == "no_principal"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_calling_disabled_before_accept_does_not_post(
    monkeypatch, jvvoice_settings
):
    """A grant that turns calling off after admission is not accepted."""
    reads = {"n": 0}

    async def load_call_inputs(_binding):
        reads["n"] += 1
        calling = reads["n"] == 1
        return [_owner_identity()], [_enabled_grant(calling_enabled=calling)]

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if str(request.url).endswith("/calling/credentials"):
            return httpx.Response(200, json={"access_token": _META_TOKEN})
        if str(request.url).endswith("/api/calls/disconnect"):
            return httpx.Response(200, json={"status": "disconnected"})
        raise AssertionError(request.url)

    binding = _call_binding()
    find_bindings, _ignored, _dispatched = _install_webhook_fakes(
        monkeypatch, binding, [_owner_identity()], [_enabled_grant()]
    )
    receipts = _MemoryReceipts()
    raw = _raw(_actionable_call_payload())
    async with _jvvoice_client(handler) as client:
        result = await receive_whatsapp_webhook(
            raw,
            _hook_signature(raw),
            find_bindings=find_bindings,
            load_call_inputs=load_call_inputs,
            http_client=client,
            receipt_store=receipts,
        )

    assert result == {"ok": True}
    assert reads["n"] == 2
    assert receipts.rows[0].status == "refused"
    assert receipts.rows[0].last_error == "admission_changed"
    assert not any(url.endswith("/api/calls/accept") for url in seen)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_disconnect_error_on_refuse_does_not_accept(jvvoice_settings):
    """A disconnect failure while a token is already held does not accept."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        body = json.loads(request.content)
        assert body["user_initiated"] is False
        return httpx.Response(502, json={"status": "error"})

    parsed = parse_whatsapp_call(_calls_payload(_offer_call(**{"from": "19995550000"})))
    async with _jvvoice_client(handler) as client:
        result = await handle_whatsapp_call(
            _call_binding(),
            parsed,
            [_owner_identity()],
            [_enabled_grant()],
            jvconnect_api_key=_JVCONNECT_KEY,
            http_client=client,
            receipt_store=_MemoryReceipts(),
            access_token_in_hand=_HELD_TOKEN,
        )

    assert result == {"ok": True}
    assert seen == [f"{_JVVOICE}/api/calls/disconnect"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_second_delivery_of_a_call_id_accepts_once(monkeypatch, jvvoice_settings):
    """The receipt for a call id blocks a second accept."""
    calls = {"accepts": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/calling/credentials"):
            return httpx.Response(200, json={"access_token": _META_TOKEN})
        calls["accepts"] += 1
        return httpx.Response(200, json={"status": "connected"})

    binding = _call_binding()
    find_bindings, load_call_inputs, _dispatched = _install_webhook_fakes(
        monkeypatch, binding, [_owner_identity()], [_enabled_grant()]
    )
    receipts = _MemoryReceipts()
    raw = _raw(_actionable_call_payload())
    signature = _hook_signature(raw)

    async with _jvvoice_client(handler) as client:
        first = await receive_whatsapp_webhook(
            raw,
            signature,
            find_bindings=find_bindings,
            load_call_inputs=load_call_inputs,
            http_client=client,
            receipt_store=receipts,
        )
        second = await receive_whatsapp_webhook(
            raw,
            signature,
            find_bindings=find_bindings,
            load_call_inputs=load_call_inputs,
            http_client=client,
            receipt_store=receipts,
        )

    assert first == {"ok": True}
    assert second == {"ok": True}
    assert calls["accepts"] == 1
    assert len(receipts.rows) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_racing_finds_accept_once_via_the_process_claim(
    monkeypatch, jvvoice_settings
):
    """Two finds that both miss the receipt still produce one accept."""

    class _BlindUntilCreate:
        def __init__(self) -> None:
            self.rows: list[SimpleNamespace] = []
            self.finds = 0

        async def find(self, binding_id: str, meta_message_id: str):
            self.finds += 1
            if self.finds <= 2:
                return []
            return [
                row
                for row in self.rows
                if row.binding_id == binding_id
                and row.meta_message_id == meta_message_id
            ]

        async def create(self, **fields):
            row = SimpleNamespace(**fields)
            self.rows.append(row)
            return row

    accepts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/calling/credentials"):
            return httpx.Response(200, json={"access_token": _META_TOKEN})
        accepts["n"] += 1
        return httpx.Response(200, json={"status": "connected"})

    binding = _call_binding()
    _install_webhook_fakes(
        monkeypatch, binding, [_owner_identity()], [_enabled_grant()]
    )
    store = _BlindUntilCreate()
    parsed = parse_whatsapp_call(_actionable_call_payload())
    async with _jvvoice_client(handler) as client:
        first = await handle_whatsapp_call(
            binding,
            parsed,
            [_owner_identity()],
            [_enabled_grant()],
            jvconnect_api_key=_JVCONNECT_KEY,
            http_client=client,
            receipt_store=store,
        )
        second = await handle_whatsapp_call(
            binding,
            parsed,
            [_owner_identity()],
            [_enabled_grant()],
            jvconnect_api_key=_JVCONNECT_KEY,
            http_client=client,
            receipt_store=store,
        )

    assert first == {"ok": True}
    assert second == {"ok": True}
    assert accepts["n"] == 1
    assert store.finds == 2
    assert len(store.rows) == 1
    assert _META_TOKEN not in json.dumps(
        [row.__dict__ for row in store.rows], default=str
    )


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "credentials",
    [
        httpx.Response(500, json={"error": "upstream"}),
        httpx.Response(200, content=b"not-json"),
    ],
)
async def test_credential_failure_does_not_accept(
    monkeypatch, jvvoice_settings, credentials
):
    """A failed credential response records failure and does not accept."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return credentials

    _result, _seen, receipts, _binding, dispatched = await _receive(
        monkeypatch,
        _actionable_call_payload(),
        identities=[_owner_identity()],
        grants=[_enabled_grant()],
        handler=handler,
    )

    assert dispatched["called"] is False
    assert all("/api/calls/accept" not in url for url in seen)
    assert receipts.rows[0].kind == "call"
    assert receipts.rows[0].status == "failed"
    assert _META_TOKEN not in (receipts.rows[0].last_error or "")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_jvvoice_status_other_than_connected_does_not_retry_or_bridge(
    monkeypatch, jvvoice_settings
):
    """One non-connected accept is recorded and not repeated or bridged."""
    accepts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/calling/credentials"):
            return httpx.Response(200, json={"access_token": _META_TOKEN})
        accepts["n"] += 1
        return httpx.Response(200, json={"status": "error"})

    result, _seen, receipts, _binding, dispatched = await _receive(
        monkeypatch,
        _actionable_call_payload(),
        identities=[_owner_identity()],
        grants=[_enabled_grant()],
        handler=handler,
    )

    assert result == {"ok": True}
    assert accepts["n"] == 1
    assert dispatched["called"] is False
    assert receipts.rows[0].status == "failed"
    assert "bridge" not in result


@pytest.mark.unit
def test_call_control_does_not_import_native_voice_or_add_a_router():
    """Call control stays off the voice relay and off APIRouter."""
    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "agentive"
        / "services"
        / "whatsapp_call_control.py"
    ).read_text(encoding="utf-8")
    ingress = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "agentive"
        / "services"
        / "whatsapp_ingress.py"
    ).read_text(encoding="utf-8")
    assert "native_voice" not in source
    assert "APIRouter" not in source
    assert "APIRouter" not in ingress
    assert "WhatsAppInboundReceipt" in source or "WhatsAppInboundReceipt" in ingress
