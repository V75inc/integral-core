"""W2.3: no-fit ranking names one structure to add, and keeps the note."""

from __future__ import annotations

import pytest

from app.models.edges import IS_MEMBER_OF
from app.models.nodes import User, Workspace
from app.services.agent_scope import current_scope_workspace_id
from app.services.destination_rank import rank_destinations
from app.services.no_fit_route import classify_no_fit
from app.utils.time import utc_now_iso


def test_empty_workspace_proposes_a_new_app():
    route = classify_no_fit(
        {"text": "a note", "winner": None, "candidates": []},
        open_track_count=0,
    )
    assert route["kind"] == "new_app"
    assert route["via"] == "scaffold"
    assert route["preserve"]["text"] == "a note"


def test_unrelated_tracks_propose_a_new_track():
    route = classify_no_fit(
        {
            "text": "quantum flux",
            "winner": None,
            "candidates": [
                {
                    "schema_fit": 0,
                    "app_id": "app-1",
                    "app_name": "Books",
                    "track_id": "t-1",
                }
            ],
        },
        open_track_count=2,
    )
    assert route["kind"] == "new_track"
    assert route["app_id"] == "app-1"
    assert route["preserve"]["text"] == "quantum flux"


def test_close_track_proposes_a_new_entry_type():
    route = classify_no_fit(
        {
            "text": "studio invoice extra",
            "winner": None,
            "candidates": [
                {
                    "schema_fit": 0.15,
                    "track_id": "t-inv",
                    "track_title": "Invoices",
                    "app_id": "app-1",
                }
            ],
        },
        open_track_count=1,
    )
    assert route["kind"] == "new_entry_type"
    assert route["track_id"] == "t-inv"
    assert "preserve" in route


def test_a_winner_has_no_route():
    assert (
        classify_no_fit(
            {"winner": "t-1", "text": "invoice", "candidates": []},
            open_track_count=1,
        )
        is None
    )


@pytest.mark.asyncio
async def test_rank_empty_workspace_returns_new_app_route():
    from app.services.app_graph import catalog_user, catalog_workspace

    now = utc_now_iso()
    user = await User.create(
        user_id="nofit-empty",
        display_name="Empty",
        created_at=now,
        updated_at=now,
    )
    await catalog_user(user)
    workspace = await Workspace.create(
        kind="personal",
        workspace_type="personal",
        name="Empty WS",
        name_fold="empty ws",
        created_at=now,
        updated_at=now,
    )
    await user.connect(workspace, edge=IS_MEMBER_OF, role="owner", joined_at=now)
    await catalog_workspace(workspace)
    token = current_scope_workspace_id.set(workspace.id)
    try:
        ranked = await rank_destinations(user.id, text="keep this note")
    finally:
        current_scope_workspace_id.reset(token)
    facet = ranked["facets"][0]
    assert facet["winner"] is None
    assert facet["route"]["kind"] == "new_app"
    assert facet["route"]["preserve"]["text"] == "keep this note"
