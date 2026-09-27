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
    assert route["stage"]["tool"] == "integral_scaffold"


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
    assert route["stage"]["tool"] == "integral_create_app_track"
    assert route["stage"]["args"]["app_id"] == "app-1"


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
    assert route["stage"]["args"]["action"] == "add_entry_type"
    assert route["stage"]["args"]["track_id"] == "t-inv"


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


async def _thread_for(user, workspace, session_id: str):
    from app.services.chat_threads import create_thread

    thread = await create_thread(
        user_id=user.id,
        provider_id="test",
        title="nofit",
        workspace_id=workspace.id,
    )
    thread.provider_session_id = session_id
    await thread.save()
    return thread


@pytest.mark.asyncio
async def test_rank_with_session_stores_preserve():
    from app.agentive.artifacts import get_artifact
    from app.services.app_graph import catalog_user, catalog_workspace
    from app.services.no_fit_route import PRESERVE_KEY

    now = utc_now_iso()
    user = await User.create(
        user_id="nofit-art",
        display_name="Art",
        created_at=now,
        updated_at=now,
    )
    await catalog_user(user)
    workspace = await Workspace.create(
        kind="personal",
        workspace_type="personal",
        name="Art WS",
        name_fold="art ws",
        created_at=now,
        updated_at=now,
    )
    await user.connect(workspace, edge=IS_MEMBER_OF, role="owner", joined_at=now)
    await catalog_workspace(workspace)
    session_id = "sess-nofit-art"
    await _thread_for(user, workspace, session_id)
    token = current_scope_workspace_id.set(workspace.id)
    try:
        ranked = await rank_destinations(
            user.id, text="keep this note", session_id=session_id
        )
    finally:
        current_scope_workspace_id.reset(token)
    route = ranked["facets"][0]["route"]
    assert route["artifact"]["key"] == PRESERVE_KEY
    got = await get_artifact(user_id=user.id, session_id=session_id, key=PRESERVE_KEY)
    assert got["ok"] is True
    assert got["body"] == "keep this note"
    assert got["metadata"]["status"] == "pending"


@pytest.mark.asyncio
async def test_matching_structure_files_preserve_once(monkeypatch):
    from app.services.app_graph import catalog_user, catalog_workspace
    from app.services.no_fit_route import (
        PRESERVE_KEY,
        maybe_file_preserve_after_structure,
        persist_no_fit_preserve,
    )

    now = utc_now_iso()
    user = await User.create(
        user_id="nofit-file",
        display_name="File",
        created_at=now,
        updated_at=now,
    )
    await catalog_user(user)
    workspace = await Workspace.create(
        kind="personal",
        workspace_type="personal",
        name="File WS",
        name_fold="file ws",
        created_at=now,
        updated_at=now,
    )
    await user.connect(workspace, edge=IS_MEMBER_OF, role="owner", joined_at=now)
    await catalog_workspace(workspace)
    session_id = "sess-nofit-file"
    await _thread_for(user, workspace, session_id)
    ranked = {
        "facets": [
            {
                "text": "quantum flux",
                "fields": {},
                "winner": None,
                "route": {
                    "kind": "new_track",
                    "via": "create_app_track",
                    "app_id": "app-1",
                    "preserve": {"text": "quantum flux", "fields": {}},
                },
            }
        ]
    }
    await persist_no_fit_preserve(user_id=user.id, session_id=session_id, ranked=ranked)
    calls = []

    async def _fake_file(_user_id, payload):
        calls.append(payload)
        return {"filed": True, "entry": {"id": f"e-{len(calls)}"}}

    monkeypatch.setattr("app.agentive.staging_executors._x_file_content", _fake_file)
    first = await maybe_file_preserve_after_structure(
        user_id=user.id,
        session_id=session_id,
        kind="create_track",
        payload={"app_id": "app-1", "title": "Notes"},
        result={"track": {"id": "t-new", "app_id": "app-1"}},
    )
    second = await maybe_file_preserve_after_structure(
        user_id=user.id,
        session_id=session_id,
        kind="create_track",
        payload={"app_id": "app-1"},
        result={"track": {"id": "t-new", "app_id": "app-1"}},
    )
    assert first["filed"] is True
    assert first["already"] is False
    assert first["entry_id"] == "e-1"
    assert second["already"] is True
    assert second["entry_id"] == "e-1"
    assert len(calls) == 1
    assert calls[0]["body"] == "quantum flux"
    from app.agentive.artifacts import get_artifact

    got = await get_artifact(user_id=user.id, session_id=session_id, key=PRESERVE_KEY)
    assert got["metadata"]["status"] == "filed"


@pytest.mark.asyncio
async def test_unrelated_structure_does_not_file_preserve(monkeypatch):
    from app.services.app_graph import catalog_user, catalog_workspace
    from app.services.no_fit_route import (
        maybe_file_preserve_after_structure,
        persist_no_fit_preserve,
    )

    now = utc_now_iso()
    user = await User.create(
        user_id="nofit-skip",
        display_name="Skip",
        created_at=now,
        updated_at=now,
    )
    await catalog_user(user)
    workspace = await Workspace.create(
        kind="personal",
        workspace_type="personal",
        name="Skip WS",
        name_fold="skip ws",
        created_at=now,
        updated_at=now,
    )
    await user.connect(workspace, edge=IS_MEMBER_OF, role="owner", joined_at=now)
    await catalog_workspace(workspace)
    session_id = "sess-nofit-skip"
    await _thread_for(user, workspace, session_id)
    await persist_no_fit_preserve(
        user_id=user.id,
        session_id=session_id,
        ranked={
            "facets": [
                {
                    "text": "studio invoice extra",
                    "winner": None,
                    "route": {
                        "kind": "new_entry_type",
                        "via": "entry_type",
                        "track_id": "t-inv",
                        "preserve": {"text": "studio invoice extra", "fields": {}},
                    },
                }
            ]
        },
    )
    calls = []

    async def _fake_file(_user_id, payload):
        calls.append(payload)
        return {"filed": True, "entry": {"id": "e-x"}}

    monkeypatch.setattr("app.agentive.staging_executors._x_file_content", _fake_file)
    skipped = await maybe_file_preserve_after_structure(
        user_id=user.id,
        session_id=session_id,
        kind="create_track",
        payload={"app_id": "app-1"},
        result={"track": {"id": "t-other"}},
    )
    assert skipped is None
    assert calls == []
