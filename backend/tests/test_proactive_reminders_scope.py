"""B-AGENT-03 — proactive reminders scan only the active workspace."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.agentive.api.proactive import get_user_reminders


class _T:
    def __init__(self, id_: str, title: str, workspace_id: str):
        self.id = id_
        self.title = title
        self.title_fold = title.casefold()
        self.workspace_id = workspace_id


class _E:
    def __init__(self, id_: str, title: str, track_id: str, due_date: str):
        self.id = id_
        self.title = title
        self.track_id = track_id
        self.due_date = due_date
        self.status = "open"
        self.context = {}


W1 = "n.Workspace.w1"
W2 = "n.Workspace.w2"
TRACK_W1 = _T("n.Track.w1", "Tasks W1", W1)
TRACK_W2 = _T("n.Track.w2", "Tasks W2", W2)
due_soon = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
ENTRY_W1 = _E("n.Entry.w1", "W1 reminder", TRACK_W1.id, due_soon)
ENTRY_W2 = _E("n.Entry.w2", "W2 reminder", TRACK_W2.id, due_soon)


@pytest.fixture
def patched_access(monkeypatch):
    async def fake_tracks(user_id: str):
        return [TRACK_W1, TRACK_W2]

    async def fake_entries(user_id: str, track_id: str, **_kwargs):
        if track_id == TRACK_W1.id:
            return [ENTRY_W1]
        if track_id == TRACK_W2.id:
            return [ENTRY_W2]
        return []

    monkeypatch.setattr(
        "app.services.permissions.get_user_accessible_tracks", fake_tracks
    )
    monkeypatch.setattr(
        "app.services.permissions.get_user_accessible_entries", fake_entries
    )


def _fake_request(scope_header: str):
    import types

    req = types.SimpleNamespace()
    req.headers = {"x-integral-scope": scope_header}
    req.state = types.SimpleNamespace(user=types.SimpleNamespace(id="u1"))
    return req


@pytest.mark.asyncio
async def test_reminders_filtered_by_scope_header(patched_access, monkeypatch):
    monkeypatch.setattr(
        "app.agentive.api.proactive.resolve_principal_id", lambda _req: "u1"
    )

    r = await get_user_reminders(_fake_request(f"ws:{W2}"))
    titles = [item["title"] for item in r.get("reminders", [])]
    assert "W2 reminder" in titles
    assert "W1 reminder" not in titles
    assert r.get("total_tracks") == 1
