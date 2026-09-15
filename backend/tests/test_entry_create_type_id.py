"""Explicit type_id must fail closed — never silently swap to another type.

Regression for the entry-create materialization path: when a caller passes
``type_id`` and ``EntryType.get`` misses (types not yet materialized, or a
stale id), ``create_entry_in_track`` must raise ``ResourceNotFoundError``
rather than falling back to ``"post"`` / the first available type.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.smoke


async def _bootstrap_track_with_type(email: str):
    from jvspatial.api.auth.models import UserCreate

    from app.api.auth import _get_auth_service
    from app.api.validators_common import compute_fold
    from app.models.edges import CONTAINS
    from app.models.nodes import EntryType, User
    from app.services.app_graph import (
        catalog_user,
        ensure_track_attached_content_profile,
    )
    from app.services.personal_workspace import ensure_personal_workspace
    from app.utils.time import utc_now_iso

    auth_service = _get_auth_service()
    resp = await auth_service.register_user(
        UserCreate(email=email, password="testpassword123")
    )
    node = await User.create(user_id=resp.id, display_name="Type Id Test")
    await catalog_user(node)
    ws = await ensure_personal_workspace(node)

    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.tracks import create_track

    created = await invoke_route_in_process(
        create_track,
        principal_id=resp.id,
        scope=ws.id,
        title="Type Id Track",
        visibility="private",
    )
    from app.models.nodes import Track

    track = await Track.get(created["track"]["id"])
    assert track is not None
    cp = await ensure_track_attached_content_profile(track)
    now = utc_now_iso()
    et = await EntryType.create(
        name="Post",
        name_fold=compute_fold("Post"),
        track_id=track.id,
        form_schema={"fields": []},
        is_template=False,
        created_at=now,
        updated_at=now,
    )
    await cp.connect(et, edge=CONTAINS, added_at=now)
    return resp.id, track, et


@pytest.mark.asyncio
async def test_explicit_type_id_missing_raises_not_silent_fallback():
    """Bogus type_id raises ResourceNotFoundError and creates no Entry."""
    from app.api.errors import ResourceNotFoundError
    from app.models.nodes import Entry
    from app.services.entry_create import create_entry_in_track

    user_id, track, _et = await _bootstrap_track_with_type("typeid-a@example.com")
    title = "must-not-create-with-bogus-type"

    with pytest.raises(ResourceNotFoundError):
        await create_entry_in_track(
            track=track,
            user_id=user_id,
            title=title,
            type_id="EntryType:does-not-exist",
        )

    found = await Entry.find({"context.title": title})
    assert list(found or []) == []


@pytest.mark.asyncio
async def test_explicit_type_id_retries_after_materialize(monkeypatch):
    """When get misses once, materialize then retry can recover the type."""
    from app.models.nodes import Entry, EntryType
    from app.services import entry_create as entry_create_mod

    user_id, track, et = await _bootstrap_track_with_type("typeid-b@example.com")

    calls = {"get": 0, "materialize": 0}
    original_get = EntryType.get

    async def counting_get(type_id: str):
        calls["get"] += 1
        if calls["get"] == 1:
            return None
        return await original_get(type_id)

    async def counting_materialize(_track):
        calls["materialize"] += 1
        return [et]

    monkeypatch.setattr(EntryType, "get", staticmethod(counting_get))
    monkeypatch.setattr(
        entry_create_mod, "materialize_entry_types_from_tier", counting_materialize
    )

    entry = await entry_create_mod.create_entry_in_track(
        track=track,
        user_id=user_id,
        title="retry-hit",
        type_id=et.id,
    )

    assert entry.type_id == et.id
    assert calls["get"] >= 2
    assert calls["materialize"] == 1
    found = await Entry.find({"context.title": "retry-hit"})
    assert len(list(found or [])) == 1
