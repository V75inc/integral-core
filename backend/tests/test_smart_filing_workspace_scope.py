"""B-AGENT-03 — filing resolution respects active workspace scope."""

from __future__ import annotations

import pytest

from app.services.agent_scope import current_scope_workspace_id
from app.services.filing_resolution import resolve_track_by_name


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


@pytest.fixture
def patched_tracks(monkeypatch):
    async def fake_tracks(user_id: str):
        return [PEOPLE_W1, PEOPLE_W2]

    monkeypatch.setattr(
        "app.services.permissions.get_user_accessible_tracks", fake_tracks
    )


@pytest.mark.asyncio
async def test_resolve_track_by_name_picks_active_workspace(patched_tracks):
    token = current_scope_workspace_id.set(W2)
    try:
        match = await resolve_track_by_name("u1", "People")
        assert match is not None
        track, _score = match
        assert track.id == PEOPLE_W2.id
    finally:
        current_scope_workspace_id.reset(token)
