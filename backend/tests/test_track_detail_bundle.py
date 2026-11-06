"""The detail bundle runs its reads together but reports errors in sequential order."""

import asyncio

import pytest

from app.api import tracks as T


class _Boom(Exception):
    pass


def _patch(monkeypatch, *, track, collab, types, views):
    async def get_track(request, track_id):
        return track() if callable(track) else track

    async def list_collaborators(request, track_id):
        return collab() if callable(collab) else collab

    async def list_entry_types(request, track_id=None):
        return types() if callable(types) else types

    async def list_track_views(request, track_id):
        await asyncio.sleep(0)
        return views() if callable(views) else views

    monkeypatch.setattr(T, "get_track", get_track)
    monkeypatch.setattr(T, "list_collaborators", list_collaborators)
    monkeypatch.setattr("app.api.entry_types.list_entry_types", list_entry_types)
    monkeypatch.setattr("app.api.views.list_track_views", list_track_views)
    monkeypatch.setattr(T, "validate_id", lambda *a, **k: None)


def _raiser(exc):
    def f():
        raise exc

    return f


@pytest.mark.asyncio
async def test_bundle_assembles_the_four_responses(monkeypatch):
    _patch(
        monkeypatch,
        track={"track": {"id": "t"}},
        collab={"collaborators": [1], "effective_total": 1, "caller_role": "owner"},
        types={"entry_types": [2]},
        views={"views": [3]},
    )
    out = await T.get_track_detail_bundle(object(), "t")
    assert out["track"] == {"id": "t"} and out["collaborators"] == [1]
    assert (
        out["entry_types"] == [2]
        and out["views"] == [3]
        and out["caller_role"] == "owner"
    )


@pytest.mark.asyncio
async def test_track_error_wins_over_later_errors(monkeypatch):
    first, other = _Boom("track"), ValueError("views")
    _patch(monkeypatch, track=_raiser(first), collab={}, types={}, views=_raiser(other))
    with pytest.raises(_Boom):
        await T.get_track_detail_bundle(object(), "t")


@pytest.mark.asyncio
async def test_a_later_error_is_raised_when_the_track_read_succeeds(monkeypatch):
    _patch(
        monkeypatch,
        track={"track": {}},
        collab={},
        types={},
        views=_raiser(ValueError("views")),
    )
    with pytest.raises(ValueError):
        await T.get_track_detail_bundle(object(), "t")
