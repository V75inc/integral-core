"""Track lists count entries per Track with the exact path first, then one shared stream.

count_user_accessible_entries_for_tracks used to admit every Entry row in
Python for every Track on a list. It now asks the same exact counter a single
Track's count uses (count_bound_records) for each admitted Track, and streams
only the Tracks where that counter declines (it applies only after current
protected-owner admission). Non-admitted Tracks still count zero.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import entry_access_projection as projection
from app.services import permissions


def _track(tid: str) -> SimpleNamespace:
    return SimpleNamespace(id=tid)


@pytest.mark.asyncio
async def test_exact_counts_used_and_only_declined_tracks_streamed(monkeypatch):
    tracks = [_track("t1"), _track("t2"), _track("t3"), _track("hidden")]
    exact_calls, streamed = [], []

    async def admit(user_id, kind, candidates):
        return [t for t in candidates if t.id != "hidden"]

    async def exact(user_id, track_id, query, **_kw):
        exact_calls.append((user_id, track_id, query))
        return {"t1": 5, "t2": None, "t3": 0}[track_id]

    async def stream(user_id, query, **_kw):
        streamed.append(query)
        yield [{"context": {"track_id": "t2"}}, {"context": {"track_id": "t2"}}]

    monkeypatch.setattr(permissions, "_filter_canonical_resource_inventory", admit)
    monkeypatch.setattr(projection, "count_bound_records", exact)
    monkeypatch.setattr(projection, "iter_records", stream)

    out = await permissions.count_user_accessible_entries_for_tracks("u", tracks)

    assert out == {"t1": 5, "t2": 2, "t3": 0, "hidden": 0}
    # The exact counter gets exactly the per-Track query a single count uses,
    # and is never asked about a Track admission refused.
    assert sorted(c[1] for c in exact_calls) == ["t1", "t2", "t3"]
    assert all(c[2] == {"context.track_id": c[1]} for c in exact_calls)
    assert streamed == [{"context.track_id": {"$in": ["t2"]}}]


@pytest.mark.asyncio
async def test_no_stream_when_every_track_is_counted_exactly(monkeypatch):
    async def admit(user_id, kind, candidates):
        return list(candidates)

    async def exact(user_id, track_id, query, **_kw):
        return 3

    async def stream(user_id, query, **_kw):  # pragma: no cover - must not run
        raise AssertionError("no Track should need the stream")
        yield []

    monkeypatch.setattr(permissions, "_filter_canonical_resource_inventory", admit)
    monkeypatch.setattr(projection, "count_bound_records", exact)
    monkeypatch.setattr(projection, "iter_records", stream)

    out = await permissions.count_user_accessible_entries_for_tracks(
        "u", [_track("a"), _track("b")]
    )
    assert out == {"a": 3, "b": 3}


@pytest.mark.asyncio
async def test_authority_failure_in_exact_path_propagates(monkeypatch):
    from app.exceptions import AuthorizationUnavailableError

    async def admit(user_id, kind, candidates):
        return list(candidates)

    async def exact(user_id, track_id, query, **_kw):
        raise AuthorizationUnavailableError()

    monkeypatch.setattr(permissions, "_filter_canonical_resource_inventory", admit)
    monkeypatch.setattr(projection, "count_bound_records", exact)

    with pytest.raises(AuthorizationUnavailableError):
        await permissions.count_user_accessible_entries_for_tracks("u", [_track("a")])


@pytest.mark.asyncio
async def test_nothing_admitted_counts_zero_without_reads(monkeypatch):
    async def admit(user_id, kind, candidates):
        return []

    async def exact(*_a, **_kw):  # pragma: no cover - must not run
        raise AssertionError("no exact count without admission")

    monkeypatch.setattr(permissions, "_filter_canonical_resource_inventory", admit)
    monkeypatch.setattr(projection, "count_bound_records", exact)
    out = await permissions.count_user_accessible_entries_for_tracks("u", [_track("a")])
    assert out == {"a": 0}
