"""Connector responses and the create ChangeEvent never echo credential
material stored in ``Connector.auth_state``.

Before: ``_to_response`` returned ``auth_state`` verbatim on POST / GET / LIST /
PATCH, and ``post_create_connector`` logged ``export_node(c)`` (full
``auth_state``) as the ChangeEvent ``after`` snapshot.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.schemas.agentive.connectors import redact_auth_state

_SECRETS = {
    "access_token": "ya29.secret",
    "refresh_token": "1//refresh",
    "client_secret": "cs-secret",
    "webhook_token": "wh-secret",
    "signing_secret": "sig-secret",
}
_PUBLIC = {"realm_id": "123", "environment": "sandbox", "labels": ["INBOX"]}


def _assert_no_secret(payload: dict) -> None:
    for key, value in _SECRETS.items():
        assert key not in payload, key
        assert value not in str(payload), value


@pytest.mark.smoke
def test_redactor_drops_token_and_secret_keys_only():
    """redact_auth_state strips secret keys and keeps the rest."""
    out = redact_auth_state({**_SECRETS, **_PUBLIC})
    assert out == _PUBLIC
    assert redact_auth_state(None) == {}


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_connector_wire_responses_never_echo_tokens(
    authenticated_client: AsyncClient,
):
    """POST / GET / LIST / PATCH responses carry no token material."""
    r = await authenticated_client.post(
        "/api/agentive/connectors",
        json={"kind": "jvagent", "auth_state": {**_SECRETS, **_PUBLIC}},
    )
    assert r.status_code in (200, 201), r.text
    created = r.json()
    _assert_no_secret(created["auth_state"])
    assert created["auth_state"]["realm_id"] == "123"
    connector_id = created["id"]

    r = await authenticated_client.get(f"/api/agentive/connectors/{connector_id}")
    assert r.status_code == 200, r.text
    _assert_no_secret(r.json()["auth_state"])

    r = await authenticated_client.get("/api/agentive/connectors")
    assert r.status_code == 200, r.text
    rows = [c for c in r.json()["connectors"] if c["id"] == connector_id]
    assert rows
    _assert_no_secret(rows[0]["auth_state"])

    r = await authenticated_client.patch(
        f"/api/agentive/connectors/{connector_id}", json={"sync_interval_seconds": 600}
    )
    assert r.status_code == 200, r.text
    _assert_no_secret(r.json()["auth_state"])

    # Stored state is intact server-side — only the wire copy is redacted.
    from app.agentive.nodes import Connector

    node = await Connector.get(connector_id)
    assert node is not None
    assert node.auth_state["access_token"] == "ya29.secret"


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_create_change_event_snapshot_carries_no_auth_state(
    authenticated_client: AsyncClient,
):
    """The connector.create ChangeEvent snapshot has no auth_state."""
    r = await authenticated_client.post(
        "/api/agentive/connectors",
        json={"kind": "jvagent", "auth_state": {**_SECRETS, **_PUBLIC}},
    )
    assert r.status_code in (200, 201), r.text
    connector_id = r.json()["id"]

    r = await authenticated_client.get("/api/audit-log")
    assert r.status_code == 200, r.text
    events = [
        e
        for e in r.json().get("events", [])
        if e.get("resource_id") == connector_id
        and e.get("action") == "connector.create"
    ]
    assert events, "connector.create ChangeEvent missing"
    for evt in events:
        after = evt.get("after") or (evt.get("context") or {}).get("after") or {}
        assert "auth_state" not in after
        _assert_no_secret(evt)
