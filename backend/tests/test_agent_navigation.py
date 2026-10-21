"""Navigation preserves live conversation authority without data writes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import agent_navigation as nav


@pytest.fixture
def navigation(monkeypatch):
    for key, value in (
        ("current_chat_thread_id", "thread"),
        ("current_scope_workspace_id", "workspace"),
        ("current_page_context_bound", True),
        ("current_page_context", {"route_path": "/agent"}),
    ):
        monkeypatch.setattr(nav, key, SimpleNamespace(get=lambda value=value: value))
    thread = SimpleNamespace(user_id="user", workspace_id="workspace")
    monkeypatch.setattr(
        nav, "ChatThread", SimpleNamespace(get=AsyncMock(return_value=thread))
    )
    location = SimpleNamespace(workspace_id="workspace", track_id="n.Track.collection")
    monkeypatch.setattr(nav, "locate_resource", AsyncMock(return_value=location))
    monkeypatch.setattr(
        nav, "evaluate", AsyncMock(return_value=SimpleNamespace(allowed=True))
    )
    return thread, location


@pytest.mark.asyncio
async def test_record_navigation_keeps_collection_context(navigation):
    result = await nav.open_view_for_dispatch(
        user_id="user", target_kind="entry", target_id="n.Entry.record"
    )
    assert result["navigation"] == {
        "path": "/tracks/n.Track.collection?entry=n.Entry.record",
        "workspace_id": "workspace",
        "thread_id": "thread",
    }
    assert nav.evaluate.await_args.kwargs["action"] == "entry.read"


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["owner", "workspace", "permission", "browser"])
async def test_navigation_refuses_unavailable_authority(
    navigation, monkeypatch, boundary
):
    thread, location = navigation
    if boundary == "owner":
        thread.user_id = "someone-else"
    elif boundary == "workspace":
        location.workspace_id = "elsewhere"
    elif boundary == "permission":
        nav.evaluate.return_value.allowed = False
    else:
        monkeypatch.setattr(
            nav, "current_page_context", SimpleNamespace(get=lambda: None)
        )
    result = await nav.open_view_for_dispatch(
        user_id="user", target_kind="track", target_id="n.Track.collection"
    )
    assert "navigation" not in result
    assert result["error"]


@pytest.mark.asyncio
async def test_saved_view_cannot_belong_to_another_collection(navigation, monkeypatch):
    from app.services import view_scope

    monkeypatch.setattr(
        view_scope,
        "read_scoped_view",
        AsyncMock(
            return_value=(
                SimpleNamespace(hidden=False),
                SimpleNamespace(track_id="other", workspace_id="workspace"),
            )
        ),
    )
    result = await nav.open_view_for_dispatch(
        user_id="user",
        target_kind="track",
        target_id="n.Track.collection",
        view_id="n.View.board",
    )
    assert "navigation" not in result
