"""Phase 19 — Gmail OAuth endpoint tests (EML-01)."""

from __future__ import annotations

from typing import Any, Dict

import pytest
from httpx import AsyncClient

from app.agentive.nodes import Connector
from tests.domain_apps.fixtures.gmail.mock import (
    TOKEN_EXCHANGE_RESPONSE,
    gmail_mock_transport,
)


@pytest.fixture(autouse=True)
def _gmail_env(monkeypatch):
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_ID", "m-id")
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_SECRET", "m-secret")
    monkeypatch.setenv("SECRET_KEY", "k")
    yield


@pytest.fixture(autouse=True)
def _stub_google_transport(monkeypatch):
    """Patch httpx.AsyncClient to default to the Gmail mock transport."""
    import httpx as _httpx

    transport = gmail_mock_transport()
    original_init = _httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs.setdefault("transport", transport)
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(_httpx.AsyncClient, "__init__", patched_init)
    yield


@pytest.mark.asyncio
async def test_oauth_start_returns_consent_url(
    authenticated_client: AsyncClient, test_user
):
    resp = await authenticated_client.post(
        "/api/agentive/connectors/gmail/oauth/start", json={}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["consent_url"].startswith("https://accounts.google.com")
    assert (
        "scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.readonly"
        in body["consent_url"]
    )
    # No client secret leakage.
    assert "m-secret" not in body["consent_url"]


@pytest.mark.asyncio
async def test_oauth_callback_persists_tokens_serverside_and_redacts_response(
    authenticated_client: AsyncClient, test_user
):
    start = await authenticated_client.post(
        "/api/agentive/connectors/gmail/oauth/start", json={}
    )
    state = start.json()["state"]
    resp = await authenticated_client.post(
        "/api/agentive/connectors/gmail/oauth/callback",
        json={"code": "mock-code", "state": state},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    body_str = str(body)
    assert TOKEN_EXCHANGE_RESPONSE["access_token"] not in body_str
    assert TOKEN_EXCHANGE_RESPONSE["refresh_token"] not in body_str
    assert "access_token" not in body["auth_state"]
    assert "refresh_token" not in body["auth_state"]
    c = await Connector.get(body["connector_id"])
    assert c.subclass_slug == "gmail"
    assert c.auth_state["access_token"] == TOKEN_EXCHANGE_RESPONSE["access_token"]
    assert c.auth_state["refresh_token"] == TOKEN_EXCHANGE_RESPONSE["refresh_token"]
    assert c.auth_state["labels"] == []  # not yet selected
    assert c.auth_state["consent_acknowledged"] is False


@pytest.mark.asyncio
async def test_oauth_callback_rejects_bogus_state(
    authenticated_client: AsyncClient, test_user
):
    resp = await authenticated_client.post(
        "/api/agentive/connectors/gmail/oauth/callback",
        json={"code": "mock-code", "state": "bogus.sig"},
    )
    assert resp.status_code == 401, resp.text
    assert resp.json().get("error_code") == "connector.auth_failed"


@pytest.mark.asyncio
async def test_gmail_labels_endpoint_lists_labels(
    authenticated_client: AsyncClient, test_user
):
    # Complete OAuth first.
    start = await authenticated_client.post(
        "/api/agentive/connectors/gmail/oauth/start", json={}
    )
    cb = await authenticated_client.post(
        "/api/agentive/connectors/gmail/oauth/callback",
        json={"code": "mock-code", "state": start.json()["state"]},
    )
    cid = cb.json()["connector_id"]

    labels_resp = await authenticated_client.get(
        f"/api/agentive/connectors/{cid}/gmail/labels"
    )
    assert labels_resp.status_code == 200, labels_resp.text
    names = {l["name"] for l in labels_resp.json()["labels"]}
    assert {"Sales", "Support", "Personal", "HR"}.issubset(names)


@pytest.mark.asyncio
async def test_set_labels_persists_label_ids_and_consent(
    authenticated_client: AsyncClient, test_user
):
    start = await authenticated_client.post(
        "/api/agentive/connectors/gmail/oauth/start", json={}
    )
    cb = await authenticated_client.post(
        "/api/agentive/connectors/gmail/oauth/callback",
        json={"code": "mock-code", "state": start.json()["state"]},
    )
    cid = cb.json()["connector_id"]

    set_resp = await authenticated_client.post(
        f"/api/agentive/connectors/{cid}/gmail/labels",
        json={
            "label_ids": ["Label_Sales", "Label_Support"],
            "consent_acknowledged": True,
        },
    )
    assert set_resp.status_code == 200, set_resp.text
    body = set_resp.json()
    assert body["label_ids"] == ["Label_Sales", "Label_Support"]
    assert body["consent_acknowledged"] is True

    c = await Connector.get(cid)
    assert c.auth_state["labels"] == ["Label_Sales", "Label_Support"]
    assert c.auth_state["consent_acknowledged"] is True
    assert c.auth_state.get("consent", {}).get("acknowledged_by_user_id")
