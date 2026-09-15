"""B-AGENT-03 — learned filing defaults respect active workspace."""

from __future__ import annotations

import pytest

from app.agentive.personalization import (
    _reset_for_tests,
    get_filing_defaults,
    record_filing_acceptance,
)
from app.services.agent_scope import current_scope_workspace_id


@pytest.fixture(autouse=True)
def _clear_personalization():
    _reset_for_tests()
    yield
    _reset_for_tests()


class _T:
    def __init__(self, id_: str, workspace_id: str):
        self.id = id_
        self.title = "Contacts"
        self.title_fold = "contacts"
        self.workspace_id = workspace_id


W1 = "n.Workspace.w1"
W2 = "n.Workspace.w2"
TRACK_W1 = _T("n.Track.w1-contacts", W1)
TRACK_W2 = _T("n.Track.w2-contacts", W2)


@pytest.fixture
def patched_tracks(monkeypatch):
    async def fake_tracks(user_id: str):
        ws = current_scope_workspace_id.get()
        if ws == W2:
            return [TRACK_W2]
        if ws == W1:
            return [TRACK_W1]
        return [TRACK_W1, TRACK_W2]

    monkeypatch.setattr(
        "app.services.permissions.get_user_accessible_tracks", fake_tracks
    )


@pytest.mark.asyncio
async def test_learned_default_from_other_workspace_ignored(patched_tracks):
    text = "quarterly planning roadmap deliverables milestones"
    await record_filing_acceptance(
        user_id="u1",
        text=text,
        track_id=TRACK_W1.id,
        entry_type_name="Task",
    )

    token = current_scope_workspace_id.set(W2)
    try:
        defaults = await get_filing_defaults(user_id="u1", text=text)
        assert defaults is None
    finally:
        current_scope_workspace_id.reset(token)


@pytest.mark.asyncio
async def test_learned_default_applies_in_same_workspace(patched_tracks):
    text = "quarterly planning roadmap deliverables milestones"
    await record_filing_acceptance(
        user_id="u1",
        text=text,
        track_id=TRACK_W2.id,
        entry_type_name="Task",
    )

    token = current_scope_workspace_id.set(W2)
    try:
        defaults = await get_filing_defaults(user_id="u1", text=text)
        assert defaults is not None
        assert defaults["track_id"] == TRACK_W2.id
    finally:
        current_scope_workspace_id.reset(token)
