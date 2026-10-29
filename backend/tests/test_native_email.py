"""Provider boundaries. No tests contact external email providers."""

import base64
import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import ValidationError

from app.api.errors import BadRequestError
from app.schemas.workspace_email import EmailConfiguration
from app.services.email_configuration import redacted, seal, unseal
from app.services.email_events import verify
from app.services.email_providers import deliver
from app.services.email_service import EmailMessage


def test_encrypted_secrets_are_bound_to_workspace_and_connection(monkeypatch):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "00" * 32)
    snapshot = seal(
        EmailConfiguration(from_email="sender@example.com", api_key="synthetic-key"),
        "ws-a",
        "connection-a",
    )
    assert "synthetic-key" not in json.dumps(snapshot)
    assert unseal(snapshot, "ws-a").api_key == "synthetic-key"
    assert redacted(snapshot)["api_key_configured"]
    assert "api_key" not in redacted(snapshot)
    for changed, workspace in [
        (snapshot, "ws-b"),
        ({**snapshot, "connection_id": "other"}, "ws-a"),
    ]:
        with pytest.raises(BadRequestError):
            unseal(changed, workspace)


@pytest.mark.parametrize(
    "values",
    [
        {"provider": "other"},
        {"from_email": "evil\r\nBcc: victim@example.com"},
        {"from_email": "sender@example.com,other"},
        {"smtp_port": 25},
        {"smtp_host": "host\nother"},
    ],
)
def test_invalid_configuration(values):
    with pytest.raises(ValidationError):
        EmailConfiguration(**values)


def test_resend_signature_raw_bytes_and_timestamp():
    raw = b'{"type":"email.delivered"}'
    stamp = str(int(time.time()))
    secret = base64.b64encode(b"synthetic-key").decode()
    sig = base64.b64encode(
        hmac.new(
            b"synthetic-key", b"event." + stamp.encode() + b"." + raw, hashlib.sha256
        ).digest()
    ).decode()
    headers = {
        "svix-id": "event",
        "svix-timestamp": stamp,
        "svix-signature": "v1," + sig,
    }
    assert verify("resend", "whsec_" + secret, raw, headers) == "event"
    for changed, h in [
        (raw + b" ", headers),
        (raw, {**headers, "svix-timestamp": "1"}),
    ]:
        with pytest.raises(BadRequestError):
            verify("resend", "whsec_" + secret, changed, h)


def test_sendgrid_signature():
    private = ec.generate_private_key(ec.SECP256R1())
    public = base64.b64encode(
        private.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    ).decode()
    raw = b'[{"event":"delivered"}]'
    stamp = str(int(time.time()))
    signature = base64.b64encode(
        private.sign(stamp.encode() + raw, ec.ECDSA(hashes.SHA256()))
    ).decode()
    headers = {
        "x-twilio-email-event-webhook-timestamp": stamp,
        "x-twilio-email-event-webhook-signature": signature,
    }
    assert verify("sendgrid", public, raw, headers)
    with pytest.raises(BadRequestError):
        verify("sendgrid", public, raw + b"x", headers)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["resend", "sendgrid"])
async def test_acceptance_safe_errors_and_stable_identity(monkeypatch, provider):
    calls = []

    async def post(self, url, **kwargs):
        calls.append(kwargs)
        return httpx.Response(
            202 if provider == "sendgrid" else 200,
            json={"id": "provider-id"},
            headers={"x-message-id": "provider-id"},
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    config = EmailConfiguration(
        provider=provider, from_email="sender@example.com", api_key="synthetic"
    )
    message = EmailMessage(
        to="recipient@example.com", subject="Test", html="<p>Test</p>", text="Test"
    )
    outcome = await deliver(config, message, "stable-identity")
    assert outcome.status == "accepted" and outcome.provider_message_id == "provider-id"
    if provider == "resend":
        assert calls[0]["headers"]["Idempotency-Key"] == "stable-identity"

    async def reject(*args, **kwargs):
        return httpx.Response(401, text="sensitive provider details")

    monkeypatch.setattr(httpx.AsyncClient, "post", reject)
    outcome = await deliver(config, message, "stable-identity")
    assert outcome.status == "failed" and "sensitive" not in outcome.error


@pytest.mark.asyncio
async def test_read_timeout_is_unknown(monkeypatch):
    async def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("sensitive")

    monkeypatch.setattr(httpx.AsyncClient, "post", timeout)
    result = await deliver(
        EmailConfiguration(from_email="a@example.com", api_key="synthetic"),
        EmailMessage(to="b@example.com", subject="Test", html="", text=""),
        "identity",
    )
    assert result.status == "unknown" and not result.retryable


@pytest.mark.asyncio
async def test_smtp_private_destination_denied(monkeypatch):
    import socket

    from app.services import email_providers

    monkeypatch.setattr(
        email_providers,
        "socket",
        SimpleNamespace(
            SOCK_STREAM=socket.SOCK_STREAM,
            getaddrinfo=socket.getaddrinfo,
            create_connection=socket.create_connection,
        ),
    )
    monkeypatch.setattr(
        email_providers.socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 587))
        ],
    )
    result = await deliver(
        EmailConfiguration(
            provider="smtp", from_email="a@example.com", smtp_host="relay.example.com"
        ),
        EmailMessage(to="b@example.com", subject="Test", html="", text=""),
        "identity",
    )
    assert result.status == "failed" and "not allowed" in result.error


@pytest.mark.asyncio
async def test_retained_events_precede_acceptance_and_cannot_cross_workspaces(
    monkeypatch,
):
    from app.services.email_delivery import _finish, database, message_object
    from app.services.email_events import reconcile_message
    from app.services.email_providers import DeliveryOutcome

    db = database()
    for identity, workspace, connection in [
        ("own", "ws-a", "connection-a"),
        ("foreign", "ws-b", "connection-b"),
    ]:
        await db.insert_if_absent(
            "object",
            {
                "id": message_object(identity),
                "entity": "EmailMessage",
                "context": {
                    "message_id": identity,
                    "workspace_id": workspace,
                    "connection_id": connection,
                    "provider": "resend",
                    "status": "dispatching",
                    "dispatch_fence": 1,
                    "payload_enc": "encrypted",
                    "created_at": "2026-01-01",
                },
            },
        )
    await db.insert_if_absent(
        "object",
        {
            "id": "o.EmailProviderEvent.early",
            "entity": "EmailProviderEvent",
            "context": {
                "workspace_id": "ws-a",
                "connection_id": "connection-a",
                "provider_message_id": "provider-id",
                "message_hint": "",
                "status": "bounced",
            },
        },
    )
    await _finish(
        "own",
        DeliveryOutcome("accepted", provider_message_id="provider-id"),
        fence=1,
        attempt=1,
    )
    await reconcile_message("own")
    saved = await db.get("object", message_object("own"))
    assert saved["context"]["status"] == "bounced"
    await _finish(
        "own",
        DeliveryOutcome("accepted", provider_message_id="provider-id"),
        fence=1,
        attempt=1,
    )
    assert (await db.get("object", message_object("own")))["context"][
        "status"
    ] == "bounced"
    await _finish(
        "foreign",
        DeliveryOutcome("accepted", provider_message_id="provider-id"),
        fence=1,
        attempt=1,
    )
    await reconcile_message("foreign")
    assert (await db.get("object", message_object("foreign")))["context"][
        "status"
    ] == "accepted"


@pytest.mark.asyncio
async def test_draft_test_refuses_invalid_sender(monkeypatch):
    result = await deliver(
        EmailConfiguration(),
        EmailMessage(to="recipient@example.com", subject="Test", text="", html=""),
        "identity",
    )
    assert result.status == "failed"
    result = await deliver(
        EmailConfiguration(from_email="sender@example.com", api_key="synthetic"),
        EmailMessage(
            to="recipient@example.com",
            subject="Test\r\nBcc: attacker@example.com",
            text="",
            html="",
        ),
        "identity",
    )
    assert result.status == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("tls", ["starttls", "implicit"])
@pytest.mark.parametrize("failure", ["", "auth", "data"])
async def test_smtp_tls_acceptance_and_failure_classification(
    monkeypatch, tls, failure
):
    import smtplib
    import socket
    import ssl

    calls = []

    class Socket:
        def close(self):
            pass

    class Context:
        def wrap_socket(self, sock, *, server_hostname):
            calls.append(("tls", server_hostname))
            return sock

    class SMTP:
        def __init__(self, host, port, timeout, context=None):
            self.context = context
            self._get_socket(host, port, timeout)

        def ehlo(self):
            calls.append(("ehlo",))

        def starttls(self, context):
            calls.append(("starttls",))

        def login(self, user, password):
            calls.append(("login", user))
            if failure == "auth":
                raise smtplib.SMTPAuthenticationError(535, b"sensitive")

        def send_message(self, message):
            calls.append(("message", str(message["To"])))
            if failure == "data":
                raise smtplib.SMTPServerDisconnected("uncertain")
            return {}

        def close(self):
            calls.append(("close",))

    from app.services import email_providers

    monkeypatch.setattr(
        email_providers,
        "socket",
        SimpleNamespace(
            SOCK_STREAM=socket.SOCK_STREAM,
            getaddrinfo=socket.getaddrinfo,
            create_connection=socket.create_connection,
        ),
    )
    monkeypatch.setattr(
        email_providers.socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 587))
        ],
    )

    def connect(address, timeout):
        assert address[0] == "93.184.216.34"
        return Socket()

    monkeypatch.setattr(email_providers.socket, "create_connection", connect)
    monkeypatch.setattr(smtplib, "SMTP", SMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", SMTP)
    monkeypatch.setattr(
        email_providers, "ssl", SimpleNamespace(create_default_context=Context)
    )
    config = EmailConfiguration(
        provider="smtp",
        from_email="sender@example.com",
        smtp_host="relay.example.com",
        smtp_username="user",
        smtp_password="synthetic",
        smtp_tls=tls,
    )
    outcome = await deliver(
        config,
        EmailMessage(
            to="recipient@example.com",
            subject="Test",
            html="<p>Hello</p>",
            text="Hello",
        ),
        "smtp-identity",
    )
    assert (
        outcome.status == {"": "accepted", "auth": "failed", "data": "unknown"}[failure]
    )
    assert not outcome.retryable
    assert (
        ("starttls",) in calls
        if tls == "starttls"
        else ("tls", "relay.example.com") in calls
    )
    assert ("close",) in calls


@pytest.mark.asyncio
async def test_smtp_private_relay_requires_operator_opt_in(monkeypatch):
    import socket

    monkeypatch.setenv("INTEGRAL_SMTP_TRUSTED_HOSTS", "relay.internal")
    from app.services import email_providers

    monkeypatch.setattr(
        email_providers,
        "socket",
        SimpleNamespace(
            SOCK_STREAM=socket.SOCK_STREAM,
            getaddrinfo=socket.getaddrinfo,
            create_connection=socket.create_connection,
        ),
    )
    monkeypatch.setattr(
        email_providers.socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 587))
        ],
    )

    def refused(*a, **k):
        raise ConnectionRefusedError()

    monkeypatch.setattr(email_providers.socket, "create_connection", refused)
    result = await deliver(
        EmailConfiguration(
            provider="smtp", from_email="a@example.com", smtp_host="relay.internal"
        ),
        EmailMessage(to="b@example.com", subject="Test", html="", text=""),
        "identity",
    )
    assert result.status == "failed" and result.retryable


@pytest.mark.asyncio
async def test_metadata_retention_and_payload_expiration(monkeypatch):
    from datetime import datetime, timedelta, timezone

    from app.services.email_delivery import (
        database,
        message_object,
        purge_email_history,
    )

    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=100)).isoformat()
    pending = (now - timedelta(days=8)).isoformat()
    await database().insert_if_absent(
        "object",
        {
            "id": "o.PlatformEmailAttempt.old",
            "entity": "PlatformEmailAttempt",
            "context": {"created_at": old},
        },
    )
    await database().insert_if_absent(
        "object",
        {
            "id": message_object("expired"),
            "entity": "EmailMessage",
            "context": {
                "created_at": pending,
                "status": "queued",
                "payload_enc": "encrypted",
            },
        },
    )
    await purge_email_history()
    assert await database().get("object", "o.PlatformEmailAttempt.old") is None
    row = await database().get("object", message_object("expired"))
    assert row["context"]["payload_enc"] == "" and row["context"]["status"] == "failed"


@pytest.mark.asyncio
async def test_platform_sendgrid_redirect_does_not_claim_acceptance(monkeypatch):
    from app.services import email_service

    monkeypatch.setattr(email_service.settings, "EMAIL_PROVIDER", "sendgrid")
    monkeypatch.setattr(email_service.settings, "SENDGRID_API_KEY", "synthetic")

    async def redirect(*args, **kwargs):
        return httpx.Response(302, headers={"location": "https://example.com"})

    monkeypatch.setattr(httpx.AsyncClient, "post", redirect)
    assert not await email_service.send_email(
        EmailMessage(
            to="recipient@example.com",
            subject="Test",
            html="",
            text="",
            source_kind="email_verification",
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source", ["password_reset", "email_verification", "member_provision"]
)
async def test_authentication_content_cannot_enter_workspace_queue(source):
    from app.services.email_delivery import enqueue_email

    with pytest.raises(BadRequestError):
        await enqueue_email(
            EmailMessage(
                to="recipient@example.com",
                subject="Sensitive",
                text="secret",
                html="",
                workspace_id="workspace",
                actor_user_id="actor",
                source_kind=source,
            ),
            idempotency_key="sensitive",
        )


@pytest.mark.asyncio
async def test_source_revocation_blocks_notifications_and_invitations():
    from jvspatial.api.exceptions import InsufficientPermissionsError

    from app.services.app_graph import create_notification
    from app.services.email_delivery import _admit_source
    from app.services.invitations import create_invitation, revoke_invitation
    from tests.test_resource_location import graph

    actor, workspace, _, _, _ = await graph()
    invitation, _, _ = await create_invitation(
        workspace=workspace,
        inviter_user_id=actor.id,
        email="invited@example.com",
        send_email_notification=False,
    )
    message = EmailMessage(
        to="invited@example.com",
        subject="Invite",
        html="",
        text="",
        workspace_id=workspace.id,
        actor_user_id=actor.id,
        source_kind="invitation",
        source_id=invitation.id,
    )
    await _admit_source(message)
    await revoke_invitation(invitation, actor_user_id=actor.id)
    with pytest.raises(InsufficientPermissionsError):
        await _admit_source(message)
    notification = await create_notification(
        user_id=actor.id,
        type="system",
        content="Example",
        metadata={"email_workspace_id": workspace.id},
    )
    message.source_kind = "notification"
    message.source_id = notification.id
    await _admit_source(message)
    await notification.delete()
    with pytest.raises(InsufficientPermissionsError):
        await _admit_source(message)


@pytest.mark.asyncio
async def test_workspace_deletion_removes_only_its_email_records():
    from app.services.email_delivery import database, delete_workspace_email

    for workspace in ("own", "foreign"):
        for entity in (
            "EmailMessage",
            "EmailAttempt",
            "EmailConnection",
            "EmailProviderEvent",
        ):
            await database().insert_if_absent(
                "object",
                {
                    "id": f"o.{entity}.{workspace}",
                    "entity": entity,
                    "context": {"workspace_id": workspace},
                },
            )
    await delete_workspace_email("own")
    assert await database().get("object", "o.EmailMessage.own") is None
    assert await database().get("object", "o.EmailMessage.foreign") is not None


@pytest.mark.asyncio
async def test_signed_webhooks_reject_malformed_events_and_missing_keys(monkeypatch):
    from app.services.email_delivery import database
    from app.services.email_events import ingest

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "00" * 32)
    secret = base64.b64encode(b"synthetic-webhook").decode()
    snapshot = seal(
        EmailConfiguration(
            from_email="sender@example.com", webhook_key="whsec_" + secret
        ),
        "workspace",
        "connection",
    )
    await database().insert_if_absent(
        "object",
        {
            "id": "o.EmailConnection.connection",
            "entity": "EmailConnection",
            "context": {"workspace_id": "workspace", "snapshot": snapshot},
        },
    )
    for data in ("invalid-json", "[]", '{"type":"email.delivered","data":false}'):
        raw = data.encode()
        stamp = str(int(time.time()))
        sig = base64.b64encode(
            hmac.new(
                b"synthetic-webhook",
                b"event." + stamp.encode() + b"." + raw,
                hashlib.sha256,
            ).digest()
        ).decode()
        with pytest.raises(BadRequestError):
            await ingest(
                "connection",
                raw,
                {
                    "svix-id": "event",
                    "svix-timestamp": stamp,
                    "svix-signature": "v1," + sig,
                },
            )
    with pytest.raises(BadRequestError):
        await ingest("connection", b"x" * 1_000_001, {})
    snapshot = seal(
        EmailConfiguration(from_email="sender@example.com"), "workspace", "no-key"
    )
    await database().insert_if_absent(
        "object",
        {
            "id": "o.EmailConnection.no-key",
            "entity": "EmailConnection",
            "context": {"workspace_id": "workspace", "snapshot": snapshot},
        },
    )
    with pytest.raises(BadRequestError):
        await ingest("no-key", b"{}", {})


@pytest.mark.asyncio
async def test_legacy_immediate_history_excludes_bodies_and_sensitive_mail():
    from app.services.email_delivery import (
        database,
        message_object,
        retain_immediate_attempt,
    )
    from app.services.email_service import _SendOutcome

    message = EmailMessage(
        to="recipient@example.com",
        subject="Test",
        text="private content",
        html="<p>private content</p>",
        workspace_id="workspace",
        source_kind="app_message",
        actor_user_id="actor",
    )
    outcome = _SendOutcome(success=False, error="Outcome unknown", status="unknown")
    identity = await retain_immediate_attempt(
        message, "smtp", outcome, connection_id="connection"
    )
    row = await database().get("object", message_object(identity))
    assert row["context"]["status"] == "unknown"
    assert "private content" not in json.dumps(row)
    message.source_kind = "member_provision"
    identity = await retain_immediate_attempt(
        message, "smtp", outcome, connection_id="connection"
    )
    assert await database().get("object", message_object(identity)) is None
    assert (
        await database().get("object", "o.PlatformEmailAttempt." + identity) is not None
    )


def test_unsupported_webhook_provider_and_expired_sendgrid_signature():
    for provider, headers in [
        ("smtp", {}),
        ("sendgrid", {"x-twilio-email-event-webhook-timestamp": "1"}),
    ]:
        with pytest.raises(BadRequestError):
            verify(provider, "unused", b"{}", headers)


@pytest.mark.asyncio
async def test_app_facade_cannot_select_reserved_platform_email(monkeypatch):
    from unittest.mock import AsyncMock

    from app.services.hooks.registry import ToolContext

    sender = AsyncMock()
    monkeypatch.setattr("app.services.email_service.send_email", sender)
    context = ToolContext(user_id="actor", workspace_id="workspace", scope="test")
    result = await context.send_workspace_transactional_email(
        to="recipient@example.com",
        subject="Test",
        text="",
        html="",
        source_kind="password_reset",
    )
    assert result["ok"] is False
    sender.assert_not_awaited()
