"""Thin filing resolution primitives — hint-based, no text classification."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.filing_resolution import (
    resolve_entry_type_for_track,
    resolve_track_by_name,
    resolve_track_for_filing,
)


class _T:
    def __init__(self, id_: str, title: str, workspace_id: str):
        self.id = id_
        self.title = title
        self.title_fold = title.casefold()
        self.workspace_id = workspace_id


W1 = "n.Workspace.alpha"
W2 = "n.Workspace.beta"
PEOPLE_W1 = _T("n.Track.people-w1", "People", W1)
PEOPLE_W2 = _T("n.Track.people-w2", "People", W2)


def _entry_type(name: str, *, key: str = "") -> SimpleNamespace:
    # Real EntryType nodes have no top-level ``key`` attribute — the manifest
    # key lives inside form_schema._manifest_entry_type_key (see
    # content_profile_compile.py). Mirror that shape here, not a fake ``key``
    # attribute, so this fixture matches what resolve_entry_type_for_track
    # actually reads.
    return SimpleNamespace(
        id=f"n.EntryType.{name.lower()}",
        name=name,
        form_schema={
            "fields": [{"key": "body", "type": "text"}],
            "_manifest_entry_type_key": key or name.lower(),
        },
    )


@pytest.fixture
def patched_tracks(monkeypatch):
    async def fake_tracks(user_id: str):
        return [PEOPLE_W1, PEOPLE_W2]

    monkeypatch.setattr(
        "app.services.permissions.get_user_accessible_tracks", fake_tracks
    )


@pytest.mark.asyncio
async def test_resolve_track_by_name_picks_active_workspace(patched_tracks):
    from app.services.agent_scope import current_scope_workspace_id

    token = current_scope_workspace_id.set(W2)
    try:
        match = await resolve_track_by_name("u1", "People")
        assert match is not None
        track, _score = match
        assert track.id == PEOPLE_W2.id
    finally:
        current_scope_workspace_id.reset(token)


@pytest.mark.asyncio
async def test_resolve_track_for_filing_no_recency_fallback(patched_tracks):
    """Without track_id, hint, or focus, resolution fails closed."""
    result = await resolve_track_for_filing("u1")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_entry_type_requires_hint(monkeypatch):
    track = MagicMock()
    note = _entry_type("Note")
    person = _entry_type("PersonRecord")

    async def fake_profile(t):
        cp = MagicMock()

        async def nodes(**kwargs):
            return [note, person]

        cp.nodes = nodes
        return cp, {}, None

    monkeypatch.setattr(
        "app.services.filing_resolution.resolve_track_runtime_profile",
        fake_profile,
    )

    assert await resolve_entry_type_for_track(track, type_hint=None) is None
    assert await resolve_entry_type_for_track(track, type_hint="Note") is note


@pytest.mark.asyncio
async def test_resolve_entry_type_matches_manifest_key(monkeypatch):
    """type_hint matching the profile.yaml KEY resolves, not just the NAME.

    Regression for a live bug: ``pay_run`` (the manifest key) never matched
    an entry type displayed as "Pay run" (the name) — casefold("pay run")
    contains neither "pay_run" as a substring nor starts with it, and the
    old code read a top-level ``et.key`` attribute EntryType nodes don't
    have (always None), so key-matching was dead for every track in the
    system. tool_manifest.yaml documents type_hint as "name or key" —  this
    proves the key half actually works.
    """
    track = MagicMock()
    pay_run = _entry_type("Pay run", key="pay_run")

    async def fake_profile(t):
        cp = MagicMock()

        async def nodes(**kwargs):
            return [pay_run]

        cp.nodes = nodes
        return cp, {}, None

    monkeypatch.setattr(
        "app.services.filing_resolution.resolve_track_runtime_profile",
        fake_profile,
    )

    assert await resolve_entry_type_for_track(track, type_hint="pay_run") is pay_run
    # Name-based matching must keep working too.
    assert await resolve_entry_type_for_track(track, type_hint="Pay run") is pay_run


@pytest.mark.asyncio
async def test_resolve_entry_type_does_not_score_text(monkeypatch):
    """Text content does not influence entry type when hint is absent."""
    track = MagicMock()
    person = _entry_type(
        "PersonRecord",
    )
    note = _entry_type("Note")

    async def fake_profile(t):
        cp = MagicMock()

        async def nodes(**kwargs):
            return [note, person]

        cp.nodes = nodes
        return cp, {}, None

    monkeypatch.setattr(
        "app.services.filing_resolution.resolve_track_runtime_profile",
        fake_profile,
    )

    assert await resolve_entry_type_for_track(track, type_hint=None) is None
