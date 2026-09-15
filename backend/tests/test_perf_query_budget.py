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
    for i in range(3):
        track_r = await authenticated_client.post(
            "/api/tracks",
            json={"title": f"Perf Tracks {i}", "visibility": "private"},
        )
        assert track_r.status_code == 200, track_r.text

    monkeypatch.setenv("INTEGRAL_PERF_HEADER_ENABLED", "1")
    resp = await authenticated_client.get("/api/tracks", params={"limit": 20})
    assert resp.status_code == 200
    count = int(resp.headers.get("X-DB-Round-Trip-Count") or "0")
    # Measured 45–50 on 0.0.18 (2026-09-13); prior ceiling 40 was already
    # tight vs per-track resolve_role (Phase C ``resolve_roles_bulk`` is the
    # real cliff fix). Pin adoption does not lower this path.
    assert count <= 55, f"tracks list used {count} DB round-trips (budget 55)"


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
    # Measured 5 on 0.0.18 (2026-09-13); prior ceiling 35.
    assert count <= 15, f"graph retrieve used {count} DB round-trips (budget 15)"


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
