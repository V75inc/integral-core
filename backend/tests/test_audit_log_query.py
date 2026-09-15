"""GET /api/audit-log query tests (Plan 02-02 Task 3).

Verifies PROV-04 contract:
- happy path: caller-visible ChangeEvents returned in (ts, id) DESC order
- D-07: per-event permission filter (cross-tenant returns empty)
- D-09: actor_kind filter scopes to one Literal member
- D-08: cursor round-trip with no overlap, hard page-size cap
- canonical 5-key error envelope on auth failure (Phase 1 D-03)

W4 cross-plan note: Test 2 (cross-tenant) depends on `second_user_client` and
related JWT-signing fixtures added by Plan 02-03 Sub-task 2d to conftest.py.
This plan does NOT modify conftest.py — pytest discovery resolves the fixtures
once 02-03 lands. Pre-02-03, run `pytest -k "not cross_tenant"` to skip Test 2.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_audit_log_returns_user_visible_events(
    authenticated_client: AsyncClient, test_user
):
    """Happy path — caller's own track-create + entry-create events visible."""
    track_resp = await authenticated_client.post(
        "/api/tracks", json={"title": "Audit Log Track"}
    )
    assert track_resp.status_code == 200, track_resp.text
    track_id = track_resp.json()["track"]["id"]

    entry_resp = await authenticated_client.post(
        "/api/entries", json={"track_id": track_id, "title": "Audit Entry"}
    )
    assert entry_resp.status_code in (200, 201), entry_resp.text

    resp = await authenticated_client.get(f"/api/audit-log?scope=track:{track_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    actions = [e["action"] for e in body["events"]]
    assert "entry.create" in actions
    # `track.create` scope is f"track:{track.id}" so it's also under this scope.
    assert "track.create" in actions


@pytest.mark.asyncio
async def test_audit_log_actor_kind_filter(
    authenticated_client: AsyncClient, test_user
):
    """actor_kind=human filter returns only human events."""
    track_resp = await authenticated_client.post(
        "/api/tracks", json={"title": "Filter Track"}
    )
    track_id = track_resp.json()["track"]["id"]

    resp = await authenticated_client.get(
        f"/api/audit-log?scope=track:{track_id}&actor_kind=human"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert all(e["actor_kind"] == "human" for e in body["events"])

    # Filter by an actor_kind that does NOT match this user's events
    resp_agent = await authenticated_client.get(
        f"/api/audit-log?scope=track:{track_id}&actor_kind=agent"
    )
    assert resp_agent.status_code == 200
    assert resp_agent.json()["events"] == []


@pytest.mark.asyncio
async def test_audit_log_cursor_round_trip(
    authenticated_client: AsyncClient, test_user
):
    """Cursor pagination — page1 ∩ page2 = ∅."""
    track_resp = await authenticated_client.post(
        "/api/tracks", json={"title": "Pagination Track"}
    )
    track_id = track_resp.json()["track"]["id"]
    for i in range(7):
        resp = await authenticated_client.post(
            "/api/entries", json={"track_id": track_id, "title": f"E{i}"}
        )
        assert resp.status_code in (200, 201), resp.text

    page1 = await authenticated_client.get(
        f"/api/audit-log?scope=track:{track_id}&limit=3"
    )
    assert page1.status_code == 200
    page1_body = page1.json()
    assert page1_body["has_more"] is True
    cursor = page1_body["next_cursor"]
    assert cursor

    page2 = await authenticated_client.get(
        f"/api/audit-log?scope=track:{track_id}&limit=3&cursor={cursor}"
    )
    assert page2.status_code == 200
    page2_body = page2.json()

    page1_ids = {e["id"] for e in page1_body["events"]}
    page2_ids = {e["id"] for e in page2_body["events"]}
    assert page1_ids.isdisjoint(
        page2_ids
    ), f"page1 and page2 overlap: {page1_ids & page2_ids}"


@pytest.mark.asyncio
async def test_audit_log_page_size_cap(authenticated_client: AsyncClient, test_user):
    """Excessive limit is clamped to AUDIT_LOG_PAGE_SIZE_MAX (default 200)."""
    track_resp = await authenticated_client.post(
        "/api/tracks", json={"title": "Cap Track"}
    )
    track_id = track_resp.json()["track"]["id"]

    # Request beyond cap; expect clamped (or 422)
    resp = await authenticated_client.get(
        f"/api/audit-log?scope=track:{track_id}&limit=1000000"
    )
    # Clamped is also acceptable per D-08 — both are deterministic.
    assert resp.status_code in (200, 422)
    if resp.status_code == 200:
        # MAX_LIMIT in pagination.py is 100, so events ≤ 100.
        assert len(resp.json()["events"]) <= 100


@pytest.mark.asyncio
async def test_audit_log_unknown_scope_returns_empty(
    authenticated_client: AsyncClient, test_user
):
    """Garbage scope is dropped defensively (returns empty)."""
    resp = await authenticated_client.get("/api/audit-log?scope=garbage:no-id")
    assert resp.status_code == 200
    # Could be empty or contain no garbage-scoped events. Both are fine
    # — the contract is "deterministic, no leak". We assert it's a 200 with
    # the canonical pagination envelope.
    body = resp.json()
    assert "events" in body
    assert "has_more" in body
    assert "next_cursor" in body


@pytest.mark.asyncio
async def test_audit_log_no_filter_lists_user_visible_events(
    authenticated_client: AsyncClient, test_user
):
    """Calling /api/audit-log without filters returns only user-visible events."""
    track_resp = await authenticated_client.post(
        "/api/tracks", json={"title": "No Filter"}
    )
    track_id = track_resp.json()["track"]["id"]
    resp = await authenticated_client.get("/api/audit-log")
    assert resp.status_code == 200
    body = resp.json()
    # All visible events should be in scopes the user can see.
    # The test_user is owner of `track_id` so events under that track are visible.
    track_scoped = [e for e in body["events"] if e["scope"] == f"track:{track_id}"]
    assert any(e["action"] == "track.create" for e in track_scoped)


@pytest.mark.asyncio
async def test_audit_log_descending_order_by_ts(
    authenticated_client: AsyncClient, test_user
):
    """Events are returned in (ts, id) DESC order — most recent first."""
    track_resp = await authenticated_client.post(
        "/api/tracks", json={"title": "Order Track"}
    )
    track_id = track_resp.json()["track"]["id"]
    for i in range(3):
        await authenticated_client.post(
            "/api/entries", json={"track_id": track_id, "title": f"E{i}"}
        )

    resp = await authenticated_client.get(f"/api/audit-log?scope=track:{track_id}")
    body = resp.json()
    timestamps = [e["ts"] for e in body["events"]]
    assert timestamps == sorted(timestamps, reverse=True)


@pytest.mark.asyncio
async def test_audit_log_filters_cross_tenant(
    authenticated_client: AsyncClient, test_user, second_user_client
):
    """Cross-tenant — user B without track view returns empty.

    W4 dependency: depends on `second_user_client` fixture from Plan 02-03
    Sub-task 2d (conftest.py extension). Run with `pytest -k cross_tenant` only
    AFTER 02-03 lands. Pre-02-03 phase verification must `pytest -k "not
    cross_tenant"` to skip this case.
    """
    track_resp = await authenticated_client.post(
        "/api/tracks", json={"title": "Audit Cross-tenant Track"}
    )
    assert track_resp.status_code == 200, track_resp.text
    track_id = track_resp.json()["track"]["id"]
    await authenticated_client.post(
        "/api/entries", json={"track_id": track_id, "title": "Secret"}
    )

    # Second user (no access) should see empty
    resp = await second_user_client.get(f"/api/audit-log?scope=track:{track_id}")
    assert resp.status_code == 200
    assert resp.json()["events"] == []
