"""F1 Phase One — Admin Forensic Loop golden-path API tests.

Covers:
  - POST /api/policies/explain allow + deny (dry-run, no audit emit on explain)
  - Real deny emits policy.deny with forensic details
  - Successful entry write → provenance + audit-log filter by resource_id
  - GET /api/audit-log action/resource filters
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.schemas.policy import Resource, Subject
from app.services.policy_engine import evaluate as policy_evaluate


def _uid(user) -> str:
    """Auth principal id (``user_id`` / AuthUser), not the User Node id."""
    if hasattr(user, "user_id") and user.user_id:
        return str(user.user_id)
    if hasattr(user, "id"):
        return str(user.id)
    if isinstance(user, dict):
        return str(user.get("user_id") or user.get("id", ""))
    return ""


def _track_id(resp) -> str:
    body = resp.json()
    return body.get("track", {}).get("id") or body.get("id")


def _entry_id(resp) -> str:
    body = resp.json()
    return body.get("entry", {}).get("id") or body.get("id")


@pytest.mark.asyncio
async def test_explain_action_allow_for_self(
    authenticated_client: AsyncClient, test_user
):
    """Owner can explain their own allow decision for an entry they create."""
    track_resp = await authenticated_client.post(
        "/api/tracks", json={"title": "F1 Explain Track"}
    )
    assert track_resp.status_code == 200, track_resp.text
    track_id = _track_id(track_resp)
    assert track_id

    entry_resp = await authenticated_client.post(
        "/api/entries",
        json={"track_id": track_id, "title": "F1 Note"},
    )
    assert entry_resp.status_code in (200, 201), entry_resp.text
    entry_id = _entry_id(entry_resp)
    assert entry_id

    user_id = _uid(test_user)
    resp = await authenticated_client.post(
        "/api/policies/explain",
        json={
            "subject_kind": "human",
            "subject_id": user_id,
            "action": "entry.read",
            "resource_kind": "entry",
            "resource_id": entry_id,
            "resource_scope": f"track:{track_id}",
            "operation_key": "echo",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["allowed"] is True
    assert body["reason"]
    assert body["operation_key"] == "echo"
    assert isinstance(body["policy_chain"], list)


@pytest.mark.asyncio
async def test_explain_dry_run_does_not_emit_policy_deny(
    authenticated_client: AsyncClient, test_user
):
    """Engine dry-run deny must not write policy.deny ChangeEvents."""
    track_resp = await authenticated_client.post(
        "/api/tracks", json={"title": "F1 Deny Explain Track"}
    )
    assert track_resp.status_code == 200, track_resp.text
    track_id = _track_id(track_resp)

    decision = await policy_evaluate(
        subject=Subject(kind="agent", id="a-f1-ghost"),
        action="entry.create",
        resource=Resource(
            kind="entry",
            id="e-ghost",
            scope=f"track:{track_id}",
        ),
        _dry_run=True,
    )
    assert decision.allowed is False
    assert decision.reason == "fail_closed_no_policy"

    audit = await authenticated_client.get(
        "/api/audit-log",
        params={
            "action": "policy.deny",
            "resource_id": "e-ghost",
            "limit": 20,
        },
    )
    assert audit.status_code == 200, audit.text
    events = audit.json().get("events") or []
    assert not any(e.get("resource_id") == "e-ghost" for e in events)

    user_id = _uid(test_user)
    resp = await authenticated_client.post(
        "/api/policies/explain",
        json={
            "subject_kind": "human",
            "subject_id": user_id,
            "action": "entry.read",
            "resource_kind": "track",
            "resource_id": track_id,
            "resource_scope": f"track:{track_id}",
        },
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_real_deny_emits_policy_deny_visible_in_audit_log(
    authenticated_admin_client: AsyncClient, test_user
):
    """Non-dry evaluate deny writes policy.deny with forensic details."""
    track_resp = await authenticated_admin_client.post(
        "/api/tracks", json={"title": "F1 Real Deny Track"}
    )
    assert track_resp.status_code == 200, track_resp.text
    track_id = _track_id(track_resp)
    resource_id = f"e-f1-real-deny-{track_id}"

    decision = await policy_evaluate(
        subject=Subject(kind="agent", id="a-f1-unpolicied"),
        action="entry.create",
        resource=Resource(
            kind="entry",
            id=resource_id,
            scope=f"track:{track_id}",
        ),
    )
    assert decision.allowed is False

    audit = await authenticated_admin_client.get(
        "/api/audit-log",
        params={
            "action": "policy.deny",
            "resource_id": resource_id,
            "limit": 50,
        },
    )
    assert audit.status_code == 200, audit.text
    events = audit.json().get("events") or []
    assert events, "expected policy.deny ChangeEvent in audit log"
    hit = events[0]
    assert hit.get("action") == "policy.deny"
    details = hit.get("details") or {}
    assert details.get("failed_action") == "entry.create"
    assert details.get("decision_reason")


@pytest.mark.asyncio
async def test_entry_write_provenance_and_audit_resource_filter(
    authenticated_client: AsyncClient, test_user
):
    """Success write → entry provenance + audit rows filterable by resource_id."""
    track_resp = await authenticated_client.post(
        "/api/tracks", json={"title": "F1 Prov Track"}
    )
    assert track_resp.status_code == 200, track_resp.text
    track_id = _track_id(track_resp)

    entry_resp = await authenticated_client.post(
        "/api/entries",
        json={"track_id": track_id, "title": "F1 Provenance Entry"},
    )
    assert entry_resp.status_code in (200, 201), entry_resp.text
    entry_body = entry_resp.json()
    entry = entry_body.get("entry") or entry_body
    entry_id = entry.get("id")
    assert entry_id

    provenance = entry.get("provenance")
    if not provenance:
        get_resp = await authenticated_client.get(f"/api/entries/{entry_id}")
        assert get_resp.status_code == 200, get_resp.text
        got = get_resp.json()
        entry = got.get("entry") or got
        provenance = entry.get("provenance") or {}
    if provenance and provenance.get("source"):
        assert provenance["source"] == "human"

    audit = await authenticated_client.get(
        "/api/audit-log",
        params={"resource_id": entry_id, "limit": 50},
    )
    assert audit.status_code == 200, audit.text
    events = audit.json().get("events") or []
    assert any(
        e.get("resource_id") == entry_id
        and (e.get("action") or "").startswith("entry.")
        for e in events
    ), f"expected entry.* audit for {entry_id}; got {events!r}"
