"""View-aware entry create resolution — calendar, kanban, and feed views."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.view_create_resolution import (
    extract_date_iso,
    normalize_calendar_mapping,
    resolve_calendar_create_entry_type_key,
    resolve_create_params_for_view,
    resolve_view_create_entry_type_key,
    resolve_view_for_filing,
)


def _entry_type(name: str, fields: list) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"n.EntryType.{name}",
        name=name,
        form_schema={"fields": [{"key": k, "type": t} for k, t in fields]},
    )


def _view(
    *,
    name: str = "Calendar",
    view_type: str = "calendar",
    config: dict | None = None,
    entry_type_keys: list | None = None,
    default_entry_type_key: str = "",
    view_id: str = "n.View.cal",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=view_id,
        name=name,
        type=view_type,
        config=config or {},
        entry_type_keys=entry_type_keys or [],
        default_entry_type_key=default_entry_type_key,
    )


def test_normalize_calendar_mapping_snake_and_camel():
    mapping = normalize_calendar_mapping(
        {"calendar_mapping": {"dateField": "custom_fields.target_date"}}
    )
    assert mapping["date_field"] == "target_date"


def test_extract_date_iso_from_text_june():
    assert extract_date_iso("On 19 June add this", None, reference_year=2026) == (
        "2026-06-19"
    )


def test_extract_date_iso_prefers_fields():
    assert extract_date_iso("ignored", {"target_date": "2025-12-01"}) == "2025-12-01"


def test_resolve_calendar_create_entry_type_key_personal_goals():
    goal = _entry_type("Goal", [("target_date", "date"), ("status", "select")])
    checkin = _entry_type("CheckIn", [("date", "date")])
    view = _view(
        config={"calendar_mapping": {"dateField": "custom_fields.target_date"}},
    )
    slug = resolve_calendar_create_entry_type_key(
        view, [goal, checkin], "target_date", "goal"
    )
    assert slug == "goal"


def test_resolve_view_create_entry_type_key_default():
    view = _view(
        view_type="feed",
        default_entry_type_key="content_piece",
        entry_type_keys=["note"],
    )
    assert resolve_view_create_entry_type_key(view) == "content_piece"


@pytest.mark.asyncio
async def test_resolve_create_params_calendar_personal_goals():
    goal = _entry_type("Goal", [("target_date", "date"), ("status", "select")])
    view = _view(
        config={"calendar_mapping": {"dateField": "custom_fields.target_date"}},
    )
    track = SimpleNamespace(id="n.Track.goals")
    with patch(
        "app.services.view_create_resolution._track_default_entry_type_key",
        new=AsyncMock(return_value="goal"),
    ):
        result = await resolve_create_params_for_view(
            track,
            view,
            [goal],
            text="On 19 June: Project was tested.",
            agent_entry_type=None,
            agent_fields=None,
        )
    assert result.entry_type == "Goal"
    assert result.fields.get("target_date") == "2026-06-19"


@pytest.mark.asyncio
async def test_resolve_create_params_preserves_explicit_entry_type():
    goal = _entry_type("Goal", [("target_date", "date")])
    habit = _entry_type("Habit", [("frequency", "select")])
    view = _view(
        config={"calendar_mapping": {"dateField": "custom_fields.target_date"}},
    )
    track = SimpleNamespace(id="n.Track.goals")
    with patch(
        "app.services.view_create_resolution._track_default_entry_type_key",
        new=AsyncMock(return_value="goal"),
    ):
        result = await resolve_create_params_for_view(
            track,
            view,
            [goal, habit],
            agent_entry_type="Habit",
            agent_fields={"frequency": "daily"},
            text="On 19 June",
        )
    assert result.entry_type == "Habit"
    assert "target_date" not in result.fields


@pytest.mark.asyncio
async def test_resolve_create_params_kanban_stage_hint():
    task = _entry_type("Task", [("status", "select")])
    view = _view(
        name="Board",
        view_type="kanban",
        config={
            "group_by": "custom_fields.status",
            "kanban_columns": [{"key": "in_progress", "label": "In progress"}],
        },
        default_entry_type_key="task",
    )
    track = SimpleNamespace(id="n.Track.tasks")
    with patch(
        "app.services.view_create_resolution._track_default_entry_type_key",
        new=AsyncMock(return_value="task"),
    ):
        result = await resolve_create_params_for_view(
            track,
            view,
            [task],
            kanban_stage_hint="in_progress",
        )
    assert result.entry_type == "Task"
    assert result.fields.get("status") == "in_progress"


@pytest.mark.asyncio
async def test_resolve_create_params_feed_entry_type_keys():
    note = _entry_type("Note", [])
    page = _entry_type("Page", [])
    view = _view(
        view_type="feed",
        entry_type_keys=["note"],
        default_entry_type_key="",
    )
    track = SimpleNamespace(id="n.Track.kb")
    with patch(
        "app.services.view_create_resolution._track_default_entry_type_key",
        new=AsyncMock(return_value="page"),
    ):
        result = await resolve_create_params_for_view(track, view, [note, page])
    assert result.entry_type == "Note"


@pytest.mark.asyncio
async def test_resolve_view_for_filing_by_hint_calendar():
    cal_view = _view(name="Calendar", view_type="calendar")
    feed_view = _view(name="Feed", view_type="feed", view_id="n.View.feed")
    track = SimpleNamespace(id="n.Track.goals")
    with patch(
        "app.api.views._list_track_views",
        new=AsyncMock(return_value=[feed_view, cal_view]),
    ):
        resolved = await resolve_view_for_filing(track, view_hint="calendar view")
    assert resolved is not None
    assert resolved.id == cal_view.id


@pytest.mark.asyncio
async def test_stage_create_entry_applies_view_resolution(monkeypatch):
    from app.agentive.tooling import bindings

    goal = _entry_type("Goal", [("target_date", "date")])
    cal_view = _view(
        config={"calendar_mapping": {"dateField": "custom_fields.target_date"}},
    )
    track = SimpleNamespace(id="n.Track.goals", title="Personal Goals")

    monkeypatch.setattr(
        bindings,
        "_bound_propose_principal",
        lambda: "n.User.test",
    )

    async def fake_resolve_view(*_a, **_k):
        return cal_view

    async def fake_load_types(_track):
        return [goal]

    async def fake_resolve_params(*_a, **_k):
        from app.services.view_create_resolution import ViewCreateResolution

        return ViewCreateResolution(
            entry_type="Goal",
            fields={"target_date": "2026-06-19"},
            view_name="Calendar",
            view_type="calendar",
        )

    monkeypatch.setattr(
        "app.services.view_create_resolution.resolve_view_for_filing",
        fake_resolve_view,
    )
    monkeypatch.setattr(
        "app.services.view_create_resolution.load_entry_types_for_track",
        fake_load_types,
    )
    monkeypatch.setattr(
        "app.services.view_create_resolution.resolve_create_params_for_view",
        fake_resolve_params,
    )

    async def fake_track_get(tid):
        return track if tid == track.id else None

    monkeypatch.setattr("app.models.nodes.Track.get", fake_track_get)

    staged = await bindings._stage_create_entry(
        {
            "track_id": track.id,
            "title": "Project was tested",
            "text": "On 19 June: Project was tested.",
            "view_hint": "Calendar",
        }
    )

    assert staged["payload"]["entry_type"] == "Goal"
    assert staged["payload"]["fields"]["target_date"] == "2026-06-19"
    assert "Calendar" in staged["diff_human"]


@pytest.mark.asyncio
async def test_stage_create_entry_ignores_unresolved_ambient_focused_view(monkeypatch):
    from app.agentive.tooling import bindings
    from app.services.agent_scope import current_focused_view_id

    track = SimpleNamespace(id="n.Track.target", title="Target Track")

    monkeypatch.setattr(
        bindings,
        "_bound_propose_principal",
        lambda: "n.User.test",
    )

    async def fake_track_get(tid):
        return track if tid == track.id else None

    monkeypatch.setattr("app.models.nodes.Track.get", fake_track_get)

    async def fake_resolve_view(*_a, **_k):
        # Simulate ambient UI focus pointing at a view on another track.
        return None

    monkeypatch.setattr(
        "app.services.view_create_resolution.resolve_view_for_filing",
        fake_resolve_view,
    )

    token = current_focused_view_id.set("n.View.other-track")
    try:
        staged = await bindings._stage_create_entry(
            {
                "track_id": track.id,
                "title": "Cross-track create",
                "entry_type": "Artifact",
            }
        )
    finally:
        current_focused_view_id.reset(token)

    assert staged["payload"]["track_id"] == track.id
    assert staged["payload"]["title"] == "Cross-track create"
    assert staged["payload"]["entry_type"] == "Artifact"


@pytest.mark.asyncio
async def test_stage_create_entry_uses_valid_ambient_focused_view(monkeypatch):
    from app.agentive.tooling import bindings
    from app.services.agent_scope import current_focused_view_id
    from app.services.view_create_resolution import ViewCreateResolution

    view = _view(
        name="Board",
        view_type="kanban",
        view_id="n.View.board",
    )
    track = SimpleNamespace(id="n.Track.tasks", title="Tasks")

    monkeypatch.setattr(
        bindings,
        "_bound_propose_principal",
        lambda: "n.User.test",
    )

    async def fake_track_get(tid):
        return track if tid == track.id else None

    monkeypatch.setattr("app.models.nodes.Track.get", fake_track_get)

    async def fake_resolve_view(*_a, **kwargs):
        assert kwargs["focused_view_id"] == view.id
        return view

    async def fake_load_types(_track):
        return []

    async def fake_resolve_params(*_a, **_k):
        return ViewCreateResolution(
            entry_type="Task",
            fields={"status": "todo"},
            view_name="Board",
            view_type="kanban",
        )

    monkeypatch.setattr(
        "app.services.view_create_resolution.resolve_view_for_filing",
        fake_resolve_view,
    )
    monkeypatch.setattr(
        "app.services.view_create_resolution.load_entry_types_for_track",
        fake_load_types,
    )
    monkeypatch.setattr(
        "app.services.view_create_resolution.resolve_create_params_for_view",
        fake_resolve_params,
    )

    token = current_focused_view_id.set(view.id)
    try:
        staged = await bindings._stage_create_entry(
            {
                "track_id": track.id,
                "title": "Task from focused view",
            }
        )
    finally:
        current_focused_view_id.reset(token)

    assert staged["payload"]["entry_type"] == "Task"
    assert staged["payload"]["fields"]["status"] == "todo"
    assert "Board" in staged["diff_human"]


@pytest.mark.asyncio
async def test_stage_create_entry_rejects_unresolved_explicit_view(monkeypatch):
    from app.agentive.tooling import bindings

    track = SimpleNamespace(id="n.Track.target", title="Target Track")

    monkeypatch.setattr(
        bindings,
        "_bound_propose_principal",
        lambda: "n.User.test",
    )

    async def fake_track_get(tid):
        return track if tid == track.id else None

    monkeypatch.setattr("app.models.nodes.Track.get", fake_track_get)

    async def fake_resolve_view(*_a, **_k):
        return None

    async def fake_list_views(_track):
        return [_view(name="Feed", view_type="feed")]

    monkeypatch.setattr(
        "app.services.view_create_resolution.resolve_view_for_filing",
        fake_resolve_view,
    )
    monkeypatch.setattr(
        "app.services.view_create_resolution._list_views_for_track",
        fake_list_views,
    )

    with pytest.raises(ValueError, match="could not resolve view"):
        await bindings._stage_create_entry(
            {
                "track_id": track.id,
                "title": "Explicit bad view",
                "view_id": "n.View.missing",
            }
        )
