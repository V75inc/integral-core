"""Inherited roles must be checked anew for every entry, including inside App jobs."""

from __future__ import annotations

import pytest

from tests.test_entry_create_type_id import _bootstrap_track_with_type

pytestmark = pytest.mark.asyncio


async def _two_entries(email: str):
    from app.services.entry_create import create_entry_in_track

    user_id, track, entry_type = await _bootstrap_track_with_type(email)
    made = []
    for n in range(2):
        made.append(
            await create_entry_in_track(
                track=track, user_id=user_id, title=f"e{n}", type_id=entry_type.id
            )
        )
    return user_id, track, made


def _count_track_resolutions(monkeypatch):
    from app.services import permissions

    calls = []
    original = permissions._resolve_role_for_user

    async def counting(user, resource_type, resource_id):
        calls.append((resource_type, resource_id))
        return await original(user, resource_type, resource_id)

    monkeypatch.setattr(permissions, "_resolve_role_for_user", counting)
    return calls


async def test_a_job_resolves_the_track_role_fresh_for_each_entry(monkeypatch):
    from app.middleware.permissions_cache import app_job_fact_scope
    from app.services.permissions import resolve_role

    user_id, track, entries = await _two_entries("inherited-job@example.com")
    calls = _count_track_resolutions(monkeypatch)
    with app_job_fact_scope():
        roles = [await resolve_role(user_id, "entry", e.id) for e in entries]
    assert roles[0] == roles[1] and roles[0] in ("owner", "editor", "admin")
    assert [c for c in calls if c == ("track", track.id)] == [("track", track.id)] * 2


async def test_outside_a_job_every_entry_reads_the_track_role_afresh(monkeypatch):
    from app.services.permissions import resolve_role

    user_id, track, entries = await _two_entries("inherited-plain@example.com")
    calls = _count_track_resolutions(monkeypatch)
    for e in entries:
        await resolve_role(user_id, "entry", e.id)
    assert [c for c in calls if c == ("track", track.id)] == [("track", track.id)] * 2
