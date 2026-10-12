"""Query-budget guardrails for hot list endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_entries_query_budget(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """Listing entries should stay within a bounded DB round-trip budget."""
    track_r = await authenticated_client.post(
        "/api/tracks",
        json={"title": "Perf Budget Track", "visibility": "private"},
    )
    assert track_r.status_code == 200, track_r.text
    track_id = track_r.json()["track"]["id"]

    monkeypatch.setenv("INTEGRAL_PERF_HEADER_ENABLED", "1")
    resp = await authenticated_client.get(
        "/api/entries",
        params={"track_id": track_id, "limit": 50},
    )
    assert resp.status_code == 200
    count_header = resp.headers.get("X-DB-Round-Trip-Count")
    assert count_header is not None
    count = int(count_header)
    # Measured 14 round-trips on jvspatial 0.0.18 + derive (2026-09-13);
    # was 13 after nodes_bulk (2026-07-02). Budget leaves ~1.5x headroom.
    assert count <= 22, f"entries list used {count} DB round-trips (budget 22)"


@pytest.mark.asyncio
async def test_list_feed_query_budget(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """Feed endpoint should stay within the entries-list DB round-trip budget."""
    track_r = await authenticated_client.post(
        "/api/tracks",
        json={"title": "Perf Feed Track", "visibility": "private"},
    )
    assert track_r.status_code == 200, track_r.text
    track_id = track_r.json()["track"]["id"]
    entry_r = await authenticated_client.post(
        "/api/entries",
        json={
            "track_id": track_id,
            "title": "Feed budget probe",
            "body": "x",
            "custom_fields": {},
        },
    )
    assert entry_r.status_code == 200, entry_r.text

    monkeypatch.setenv("INTEGRAL_PERF_HEADER_ENABLED", "1")
    resp = await authenticated_client.get("/api/feed", params={"limit": 20})
    assert resp.status_code == 200
    count = int(resp.headers.get("X-DB-Round-Trip-Count") or "0")
    # Measured 29 on 0.0.18 (2026-09-13); prior ceiling 30.
    assert count <= 32, f"feed used {count} DB round-trips (budget 32)"


@pytest.mark.asyncio
async def test_list_tracks_query_budget(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """Tracks list should not scale DB round-trips linearly with page size."""
    import traceback

    monkeypatch.setenv("INTEGRAL_PERF_HEADER_ENABLED", "1")
    points = []
    for i in range(9):
        track_r = await authenticated_client.post(
            "/api/tracks", json={"title": f"Perf Tracks {i}", "visibility": "private"}
        )
        assert track_r.status_code == 200, track_r.text
        if i + 1 in {1, 3, 9}:
            resp = await authenticated_client.get("/api/tracks", params={"limit": 20})
            assert resp.status_code == 200
            points.append(int(resp.headers["X-DB-Round-Trip-Count"]))
    # 2026-10-11, pinned 0.1.1: per-resource canonical checks previously
    # used 56/76/136 ops for 1/3/9 Tracks. Fresh bounded bulk admission
    # measured 51/55/67 before candidate discovery was separated from strict
    # destructive inventory. Preserve the old 3-row ceiling and reject the
    # 8-per-row authority regression; only legacy presentation's 2-per-row
    # reads plus four fixed spare operations may scale between these pages.
    assert points[1] <= 55, f"tracks list used {points[1]} DB round-trips (budget 55)"
    assert (
        points[2] <= points[1] + 16
    ), f"tracks list authority scaled unexpectedly: {points}"


@pytest.mark.asyncio
async def test_retrieve_graph_query_budget(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """Graph retrieve should be bounded — not O(workspace entries)."""
    track_r = await authenticated_client.post(
        "/api/tracks",
        json={"title": "Perf Retrieve Track", "visibility": "private"},
    )
    assert track_r.status_code == 200, track_r.text
    track_id = track_r.json()["track"]["id"]
    await authenticated_client.post(
        "/api/entries",
        json={
            "track_id": track_id,
            "title": "Retrieve budget probe",
            "body": "keyword alpha",
            "custom_fields": {},
        },
    )

    monkeypatch.setenv("INTEGRAL_PERF_HEADER_ENABLED", "1")
    resp = await authenticated_client.post(
        "/api/retrieve",
        json={
            "mode": "graph",
            "query": "alpha",
            "scope": f"track:{track_id}",
            "top_n": 10,
        },
    )
    assert resp.status_code == 200, resp.text
    count = int(resp.headers.get("X-DB-Round-Trip-Count") or "0")
    for i in range(12):
        created = await authenticated_client.post(
            "/api/entries",
            json={
                "track_id": track_id,
                "title": f"Retrieve extra {i}",
                "body": "keyword alpha",
                "custom_fields": {},
            },
        )
        assert created.status_code == 200, created.text
    larger = await authenticated_client.post(
        "/api/retrieve",
        json={
            "mode": "graph",
            "query": "alpha",
            "scope": f"track:{track_id}",
            "top_n": 10,
        },
    )
    assert larger.status_code == 200, larger.text
    large_count = int(larger.headers["X-DB-Round-Trip-Count"])
    # 2026-10-11, pinned jvspatial 0.1.1 + fresh primary admission: 17
    # operations at both 1 and 13 Entries. Bound absolute overhead and scaling.
    assert count <= 20, f"graph retrieve used {count} DB round-trips (budget 20)"
    assert (
        large_count <= count + 2
    ), f"retrieve scaled from {count} to {large_count} operations"


@pytest.mark.asyncio
async def test_list_entries_multi_track_query_budget(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """A page spanning many tracks must not scale round-trips per track.

    Guards the bulk-traversal paths (jvspatial ``Node.nodes_bulk`` in
    ``prefetch_tracks_and_spaces_for_entries`` and the app-cascade track
    expansion in ``permissions.py``): parent-app resolution for the page is
    one edge pass + one node pass, regardless of how many distinct tracks
    the page touches.
    """
    track_ids = []
    for i in range(4):
        track_r = await authenticated_client.post(
            "/api/tracks",
            json={"title": f"Perf Multi Track {i}", "visibility": "private"},
        )
        assert track_r.status_code == 200, track_r.text
        track_ids.append(track_r.json()["track"]["id"])

    for tid in track_ids:
        for j in range(2):
            entry_r = await authenticated_client.post(
                "/api/entries",
                json={
                    "track_id": tid,
                    "title": f"Perf entry {tid[-4:]}-{j}",
                    "body": "budget probe",
                    "custom_fields": {},
                },
            )
            assert entry_r.status_code == 200, entry_r.text

    monkeypatch.setenv("INTEGRAL_PERF_HEADER_ENABLED", "1")
    resp = await authenticated_client.get("/api/entries", params={"limit": 50})
    assert resp.status_code == 200
    entries = resp.json().get("entries") or []
    assert len(entries) >= 8, f"expected the 8 seeded entries, got {len(entries)}"
    count_header = resp.headers.get("X-DB-Round-Trip-Count")
    assert count_header is not None
    count = int(count_header)
    # Measured 37 on 0.0.18 (2026-09-13); was 39 after nodes_bulk (2026-07-02).
    assert count <= 45, f"multi-track list used {count} DB round-trips (budget 45)"
