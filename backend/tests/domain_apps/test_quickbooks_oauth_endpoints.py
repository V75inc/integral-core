"""Phase 18 — QuickBooks OAuth endpoint tests (QB-01).

Confirms the two new @endpoint routes:

- POST /agentive/connectors/quickbooks/authorize → consent URL + state.
- POST /agentive/connectors/quickbooks/callback → exchange code, persist
  tokens (server-side ONLY), return redacted auth_state.

Tokens never appear in the response bodies — verified by inspecting
the wire shape. The mock Intuit transport serves the canned token
responses from ``tests/domain_apps/fixtures/quickbooks_mock.py``.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest
from httpx import AsyncClient

from app.agentive.connectors.quickbooks import QuickBooksConnector
from app.agentive.nodes import Connector
from tests.domain_apps.fixtures.quickbooks_mock import (
    REALM_ID,
    TOKEN_EXCHANGE_RESPONSE,
    quickbooks_mock_transport,
)


@pytest.fixture(autouse=True)
def _qbo_env(monkeypatch):
    monkeypatch.setenv("QUICKBOOKS_CLIENT_ID", "mock-client-id")
    monkeypatch.setenv("QUICKBOOKS_CLIENT_SECRET", "mock-client-secret")
    monkeypatch.setenv("SECRET_KEY", "mock-secret-key")
    yield


@pytest.fixture(autouse=True)
def _stub_intuit_transport(monkeypatch):
    """Patch the OAuth helper's httpx call to use the mock transport.

    The helper creates an httpx.AsyncClient internally — we intercept by
    monkeypatching httpx.AsyncClient.post to a transport-aware call.
    """
    import httpx as _httpx

    transport = quickbooks_mock_transport()
    original_init = _httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs.setdefault("transport", transport)
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(_httpx.AsyncClient, "__init__", patched_init)
    yield


@pytest.mark.asyncio
async def test_authorize_endpoint_returns_consent_url_and_state(
    authenticated_client: AsyncClient, test_user
):
    """POST /agentive/connectors/quickbooks/authorize returns Intuit URL + state."""
    resp = await authenticated_client.post(
        "/api/agentive/connectors/quickbooks/authorize", json={}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "consent_url" in body
    assert body["consent_url"].startswith("https://appcenter.intuit.com/connect/oauth2")
    assert "state" in body and "." in body["state"]
    # Client secret must NOT appear anywhere in the consent URL.
    assert "mock-client-secret" not in body["consent_url"]


@pytest.mark.asyncio
async def test_callback_endpoint_exchanges_code_and_creates_connector(
    authenticated_client: AsyncClient, test_user
):
    """POST /callback exchanges the code, creates a fresh connector, returns redacted shape."""
    # First, get a valid state token via /authorize.
    auth_resp = await authenticated_client.post(
        "/api/agentive/connectors/quickbooks/authorize", json={}
    )
    state = auth_resp.json()["state"]

    cb_resp = await authenticated_client.post(
        "/api/agentive/connectors/quickbooks/callback",
        json={"code": "mock-code", "state": state, "realmId": REALM_ID},
    )
    assert cb_resp.status_code == 200, cb_resp.text
    body = cb_resp.json()
    assert body["realm_id"] == REALM_ID
    assert body["connected"] is True
    assert body["reauth_required"] is False
    # Tokens must be REDACTED in the response body.
    body_str = str(body)
    assert TOKEN_EXCHANGE_RESPONSE["access_token"] not in body_str
    assert TOKEN_EXCHANGE_RESPONSE["refresh_token"] not in body_str
    # The redacted auth_state should carry realm_id + environment only.
    assert body["auth_state"]["realm_id"] == REALM_ID
    assert "access_token" not in body["auth_state"]
    assert "refresh_token" not in body["auth_state"]

    # Server-side: the connector now carries the tokens in auth_state.
    connector_id = body["connector_id"]
    c = await Connector.get(connector_id)
    assert c is not None
    assert c.subclass_slug == "quickbooks"
    assert c.auth_state["realm_id"] == REALM_ID
    assert c.auth_state["access_token"] == TOKEN_EXCHANGE_RESPONSE["access_token"]
    assert c.auth_state["refresh_token"] == TOKEN_EXCHANGE_RESPONSE["refresh_token"]


@pytest.mark.asyncio
async def test_callback_rejects_invalid_state(
    authenticated_client: AsyncClient, test_user
):
    """POST /callback raises 401 ConnectorAuthError when state is bogus."""
    cb_resp = await authenticated_client.post(
        "/api/agentive/connectors/quickbooks/callback",
        json={
            "code": "mock-code",
            "state": "bogus.signature",
            "realmId": REALM_ID,
        },
    )
    assert cb_resp.status_code == 401, cb_resp.text
    body = cb_resp.json()
    assert body.get("error_code") == "connector.auth_failed"
