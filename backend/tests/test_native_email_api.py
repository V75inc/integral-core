"""HTTP email configuration: success, denied and invalid boundary axes."""

import base64
import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock

import pytest

from app.schemas.workspace_email import EmailConfiguration
from app.services import email_delivery as delivery
from app.services.email_configuration import seal

pytestmark = pytest.mark.asyncio


async def test_configuration_read_and_invalid_secret_input(authenticated_client):
    response = await authenticated_client.get("/api/workspace/email")
    assert response.status_code == 200
    assert response.json()["mode"] == "platform"
    response = await authenticated_client.put(
        "/api/workspace/email/draft",
        json={
            "expected_revision": 0,
            "configuration": {"api_key": "synthetic-sensitive", "provider": "invalid"},
        },
    )
    assert response.status_code == 400
    assert "synthetic-sensitive" not in response.text
    response = await authenticated_client.get(
        "/api/workspace/email/history", params={"cursor": "foreign-cursor"}
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", ""),
        ("put", "/draft"),
        ("post", "/test"),
        ("post", "/activate"),
        ("post", "/disable"),
        ("get", "/history"),
    ],
)
async def test_anonymous_settings_denied(client, method, path):
    response = await getattr(client, method)("/api/workspace/email" + path)
    assert response.status_code in (401, 403)


async def test_foreign_workspace_denied(authenticated_client, test_user2):
    from app.services.personal_workspace import ensure_personal_workspace

    workspace = await ensure_personal_workspace(test_user2)
    workspace_id = workspace.id if hasattr(workspace, "id") else workspace
    response = await authenticated_client.get(
        "/api/workspace/email", headers={"X-Integral-Scope": f"ws:{workspace_id}"}
    )
    assert response.status_code == 403


async def test_signed_webhook_success_dedup_and_invalid(client, monkeypatch):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "00" * 32)
    secret = base64.b64encode(b"synthetic-webhook-secret").decode()
    snapshot = seal(
        EmailConfiguration(
            from_email="sender@example.com",
            api_key="synthetic",
            webhook_key="whsec_" + secret,
        ),
        "workspace-a",
        "connection-a",
    )
    await delivery.database().insert_if_absent(
        "object",
        {
            "id": "o.EmailConnection.connection-a",
            "entity": "EmailConnection",
            "context": {"workspace_id": "workspace-a", "snapshot": snapshot},
        },
    )
    raw = json.dumps(
        {"type": "email.delivered", "data": {"email_id": "provider-id"}}
    ).encode()
    stamp = str(int(time.time()))
    sig = base64.b64encode(
        hmac.new(
            b"synthetic-webhook-secret",
            b"event." + stamp.encode() + b"." + raw,
            hashlib.sha256,
        ).digest()
    ).decode()
    headers = {
        "svix-id": "event",
        "svix-timestamp": stamp,
        "svix-signature": "v1," + sig,
        "content-type": "application/json",
    }
    response = await client.post(
        "/api/workspace/email/webhooks/connection-a", content=raw, headers=headers
    )
    assert response.status_code == 200
    response = await client.post(
        "/api/workspace/email/webhooks/connection-a", content=raw, headers=headers
    )
    assert response.status_code == 200
    events = await delivery.database().find("object", {"entity": "EmailProviderEvent"})
    assert len(events) == 1
    response = await client.post(
        "/api/workspace/email/webhooks/connection-a",
        content=raw + b" ",
        headers=headers,
    )
    assert response.status_code == 400
    response = await client.post("/api/workspace/email/webhooks/unknown", content=raw)
    assert response.status_code == 400


@pytest.mark.postgres
async def test_configuration_http_lifecycle(authenticated_client, monkeypatch):
    from app.services.email_providers import DeliveryOutcome

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "00" * 32)
    monkeypatch.setattr(
        delivery,
        "deliver",
        AsyncMock(
            return_value=DeliveryOutcome("accepted", provider_message_id="test-message")
        ),
    )
    response = await authenticated_client.put(
        "/api/workspace/email/draft",
        json={
            "expected_revision": 0,
            "configuration": {
                "provider": "resend",
                "from_email": "sender@example.com",
                "api_key": "synthetic-sensitive",
            },
        },
    )
    assert response.status_code == 200
    assert "synthetic-sensitive" not in response.text
    revision = response.json()["revision"]
    response = await authenticated_client.post(
        "/api/workspace/email/activate", json={"expected_revision": revision}
    )
    assert response.status_code == 400
    response = await authenticated_client.post(
        "/api/workspace/email/test",
        json={"expected_revision": revision, "to": "chosen@example.com"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert delivery.deliver.await_args.args[1].to == "chosen@example.com"
    response = await authenticated_client.post(
        "/api/workspace/email/activate", json={"expected_revision": revision}
    )
    assert response.status_code == 200
    revision = response.json()["revision"]
    response = await authenticated_client.post(
        "/api/workspace/email/disable", json={"expected_revision": revision}
    )
    assert response.status_code == 200
    assert response.json()["mode"] == "platform"
    response = await authenticated_client.post(
        "/api/workspace/email/test", json={"expected_revision": 0}
    )
    assert response.status_code == 400
