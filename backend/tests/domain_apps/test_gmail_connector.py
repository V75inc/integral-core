"""Phase 19 — GmailConnector unit tests.

Covers sync_pull (label-scoped), to_entry (Email Thread mapping),
to_anchored_children (Email Message projection), idempotency key
shapes, conflict_policy, and OAuth token refresh / reauth.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import httpx
import pytest

from app.agentive.connectors.gmail import GmailConnector
from app.services.connectors import ExternalRecord
from tests.domain_apps.fixtures.gmail.mock import (
    THREADS,
    gmail_invalid_grant_transport,
    gmail_mock_transport,
)

# ---------------------------------------------------------------------------
# Fake Connector (in-memory)
# ---------------------------------------------------------------------------


class _FakeConnector:
    def __init__(self, *, auth_state: Dict[str, Any], sync_cursor: str = ""):
        self.id = "fake-gmail-conn-1"
        self.auth_state: Dict[str, Any] = dict(auth_state)
        self.sync_cursor = sync_cursor
        self.save_calls = 0

    async def save(self):
        self.save_calls += 1


def _fresh_auth_state(**overrides):
    base = {
        "labels": ["Label_Sales"],
        "access_token": "ok",
        "refresh_token": "rt",
        "access_token_expires_at": (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat(),
    }
    base.update(overrides)
    return base


def _near_expiry_auth_state():
    return _fresh_auth_state(
        access_token_expires_at=(
            datetime.now(timezone.utc) + timedelta(seconds=60)
        ).isoformat()
    )


@pytest.fixture(autouse=True)
def _gmail_env(monkeypatch):
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_ID", "m-id")
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_SECRET", "m-secret")
    monkeypatch.setenv("SECRET_KEY", "k")
    yield


# ---------------------------------------------------------------------------
# idempotency + conflict_policy
# ---------------------------------------------------------------------------


def test_thread_idempotency_key_uses_thread_id():
    c = GmailConnector()
    rec = ExternalRecord(external_id="t1", payload={"id": "t1"})
    k1 = c.idempotency_key_for(rec)
    rec2 = ExternalRecord(external_id="t2", payload={"id": "t2"})
    k2 = c.idempotency_key_for(rec2)
    assert k1 != k2
    assert len(k1) == 64


def test_message_idempotency_key_folds_thread_and_message_id():
    a = GmailConnector.message_idempotency_key("t1", "m1")
    b = GmailConnector.message_idempotency_key("t1", "m2")
    c = GmailConnector.message_idempotency_key("t2", "m1")
    assert len({a, b, c}) == 3


def test_conflict_policy_is_mirror_only():
    assert GmailConnector.conflict_policy == "mirror_only"


# ---------------------------------------------------------------------------
# to_entry
# ---------------------------------------------------------------------------


def test_to_entry_maps_email_thread_fields():
    c = GmailConnector()
    thread = THREADS["thread_sales_001"]
    rec = ExternalRecord(external_id="thread_sales_001", payload=thread)
    m = c.to_entry(rec)
    assert m.entry_type_key == "email_thread"
    cf = m.custom_fields
    assert cf["_source_system"] == "gmail"
    assert cf["gmail_thread_id"] == "thread_sales_001"
    assert "ap@contoso.example" in cf["participant_emails"]
    assert "sales@.example" in cf["participant_emails"]
    assert cf["message_count"] == 2
    assert cf["subject"]  # non-empty subject pulled from first message


def test_to_anchored_children_projects_each_message():
    c = GmailConnector()
    thread = THREADS["thread_sales_001"]
    rec = ExternalRecord(external_id="thread_sales_001", payload=thread)
    children = c.to_anchored_children(rec)
    assert len(children) == 2
    first = children[0]
    assert first["materialized"].entry_type_key == "email_message"
    assert first["external_id"] == "msg_sales_001_a"
    # Distinct idempotency keys per message.
    assert children[0]["idempotency_key"] != children[1]["idempotency_key"]


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_fresh_token_noop_when_valid():
    conn = _FakeConnector(auth_state=_fresh_auth_state())
    c = GmailConnector()
    async with httpx.AsyncClient(transport=gmail_mock_transport()) as client:
        ok = await c._ensure_fresh_token(conn, http_client=client)
    assert ok is True
    assert conn.save_calls == 0


@pytest.mark.asyncio
async def test_ensure_fresh_token_rotates_within_skew():
    conn = _FakeConnector(auth_state=_near_expiry_auth_state())
    c = GmailConnector()
    async with httpx.AsyncClient(transport=gmail_mock_transport()) as client:
        ok = await c._ensure_fresh_token(conn, http_client=client)
    assert ok is True
    assert conn.save_calls == 1
    # New access token from mock refresh response.
    assert conn.auth_state["access_token"] == "mock-gmail-access-token-refreshed"
    # Google doesn't always rotate refresh_token — existing one preserved.
    assert conn.auth_state["refresh_token"] == "rt"


@pytest.mark.asyncio
async def test_ensure_fresh_token_invalid_grant_marks_reauth():
    conn = _FakeConnector(auth_state=_near_expiry_auth_state())
    c = GmailConnector()
    async with httpx.AsyncClient(transport=gmail_invalid_grant_transport()) as client:
        ok = await c._ensure_fresh_token(conn, http_client=client)
    assert ok is False
    assert conn.auth_state.get("reauth_required") is True


# ---------------------------------------------------------------------------
# sync_pull — label-scoped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_pull_label_scoped_yields_only_labeled_threads():
    """Connector scoped to Label_Sales pulls only Sales threads."""
    c = GmailConnector()
    conn = _FakeConnector(auth_state=_fresh_auth_state(labels=["Label_Sales"]))
    transport = gmail_mock_transport()
    async with httpx.AsyncClient(transport=transport) as client:
        c.test_http_client = client
        records: List[ExternalRecord] = []
        async for rec in c.sync_pull(connector=conn):
            records.append(rec)
        c.test_http_client = None
    ids = {r.external_id for r in records}
    assert ids == {"thread_sales_001", "thread_sales_002"}


@pytest.mark.asyncio
async def test_sync_pull_two_labels_dedups_threads_appearing_in_both():
    c = GmailConnector()
    conn = _FakeConnector(
        auth_state=_fresh_auth_state(labels=["Label_Sales", "Label_Support"])
    )
    async with httpx.AsyncClient(transport=gmail_mock_transport()) as client:
        c.test_http_client = client
        records: List[ExternalRecord] = []
        async for rec in c.sync_pull(connector=conn):
            records.append(rec)
        c.test_http_client = None
    ids = {r.external_id for r in records}
    assert ids == {"thread_sales_001", "thread_sales_002", "thread_support_001"}
