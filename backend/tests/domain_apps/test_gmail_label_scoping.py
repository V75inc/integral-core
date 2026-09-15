"""Phase 19 — Lock I-CON-EML-01 (label-scoped Gmail ingestion).

The invariant: Gmail sync_pull MUST issue threads.list queries scoped
to auth_state.labels only. An empty labels array yields zero records
(structural no-op). No code path exists that pulls unlabeled mail.

This test locks the invariant against future regression:

1. Empty labels = zero records — no HTTP calls observed.
2. With a configured label, the connector still does NOT ingest the
   unlabeled "inbox_only" thread present in the mock account.
3. The threads.list URL the connector emits always carries a
   labelIds= query param.

Phase 19 ships no resolver-side enforcement — this lock asserts the
connector itself satisfies the structural contract.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import httpx
import pytest

from app.agentive.connectors.gmail import GmailConnector
from app.services.connectors import ExternalRecord
from tests.domain_apps.fixtures.gmail.mock import (
    GMAIL_API_BASE,
    THREADS,
    gmail_mock_transport,
)


class _FakeConnector:
    def __init__(self, *, auth_state: Dict[str, Any]):
        self.id = "fake-gmail-conn-scope"
        self.auth_state: Dict[str, Any] = dict(auth_state)
        self.sync_cursor = ""

    async def save(self):
        return None


def _valid_auth_state(labels: List[str]) -> Dict[str, Any]:
    return {
        "labels": labels,
        "access_token": "ok",
        "refresh_token": "rt",
        "access_token_expires_at": (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat(),
    }


@pytest.fixture(autouse=True)
def _gmail_env(monkeypatch):
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_ID", "m-id")
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_SECRET", "m-secret")
    monkeypatch.setenv("SECRET_KEY", "k")
    yield


@pytest.mark.asyncio
async def test_empty_labels_yields_zero_records_with_no_http_calls():
    """I-CON-EML-01 (1) — empty labels = structural no-op, no Gmail API hits."""
    captured_urls: List[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured_urls.append(str(request.url))
        return httpx.Response(200, json={"threads": []})

    c = GmailConnector()
    conn = _FakeConnector(auth_state=_valid_auth_state(labels=[]))
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
        c.test_http_client = client
        records: List[ExternalRecord] = []
        async for rec in c.sync_pull(connector=conn):
            records.append(rec)
        c.test_http_client = None

    assert records == []
    assert (
        captured_urls == []
    ), f"sync_pull issued HTTP calls with empty labels: {captured_urls}"


@pytest.mark.asyncio
async def test_inbox_only_thread_never_appears_in_label_scoped_pull():
    """I-CON-EML-01 (2) — an unlabeled inbox-only thread is never ingested
    even when the connector is scoped to a different user label.

    The Gmail mock fixture includes ``thread_inbox_only_001`` (labelIds=
    ["INBOX"] — no user label). Scoping the connector to ``Label_Sales``
    must not surface it.
    """
    c = GmailConnector()
    conn = _FakeConnector(auth_state=_valid_auth_state(labels=["Label_Sales"]))
    async with httpx.AsyncClient(transport=gmail_mock_transport()) as client:
        c.test_http_client = client
        records: List[ExternalRecord] = []
        async for rec in c.sync_pull(connector=conn):
            records.append(rec)
        c.test_http_client = None
    ids = {r.external_id for r in records}
    assert "thread_inbox_only_001" not in ids
    # And the Sales threads ARE present.
    assert "thread_sales_001" in ids
    assert "thread_sales_002" in ids


@pytest.mark.asyncio
async def test_threads_list_url_always_carries_labelIds_param():
    """I-CON-EML-01 (3) — every threads.list URL emitted carries labelIds=."""
    list_urls: List[str] = []
    sales_thread = THREADS["thread_sales_001"]

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/users/me/threads" in url and "/threads/" not in url:
            list_urls.append(url)
            return httpx.Response(
                200,
                json={"threads": [{"id": "thread_sales_001", "historyId": "1"}]},
            )
        if "/users/me/threads/" in url:
            return httpx.Response(200, json=sales_thread)
        return httpx.Response(404, json={})

    c = GmailConnector()
    conn = _FakeConnector(auth_state=_valid_auth_state(labels=["Label_Sales"]))
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
        c.test_http_client = client
        async for _ in c.sync_pull(connector=conn):
            pass
        c.test_http_client = None

    assert list_urls, "sync_pull issued no threads.list URL"
    pattern = re.compile(r"labelIds=[^&]+")
    for url in list_urls:
        assert pattern.search(url), f"threads.list URL missing labelIds= : {url}"


def test_sync_pull_source_contains_no_inbox_fallthrough_paths():
    """I-CON-EML-01 (structural grep) — the connector source never builds an
    unscoped threads.list URL.

    Concretely: every occurrence of ``users/me/threads`` in ``gmail.py``
    either (a) belongs to a per-thread GET URL (``.../threads/{...}``
    fetched by ID) OR (b) sits within 200 characters of a ``labelIds=``
    query parameter. The 200-char window covers the canonical adjacent
    f-string composition pattern.
    """
    from pathlib import Path

    source = Path("app/agentive/connectors/gmail.py").read_text(encoding="utf-8")
    # Locate every ``users/me/threads`` occurrence and inspect a window
    # around it for labelIds= or the per-thread GET pattern.
    for match in re.finditer(r"users/me/threads", source):
        idx = match.start()
        window = source[max(0, idx - 50) : idx + 250]
        is_per_thread_get = "/threads/{" in window or "threads/{quote(" in window
        carries_labels = "labelIds=" in window
        assert (
            is_per_thread_get or carries_labels
        ), f"unscoped threads.list URL near offset {idx}; window:\n{window!r}"
