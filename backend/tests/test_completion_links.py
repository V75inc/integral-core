"""Completion destinations derive from rooted results and current access."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.completion_links import (
    append_completion_links,
    completion_links,
    completion_refs,
)
from tests.test_access_perimeter_regressions import rooted_graph

pytestmark = pytest.mark.smoke


@pytest.mark.asyncio
async def test_app_uses_only_track_but_multi_track_app_uses_app():
    from app.models.edges import CONTAINS
    from app.models.nodes import Track

    owner, _, workspace, app, track, _ = await rooted_graph()
    args = dict(
        user_id=owner.id, workspace_id=workspace.id, kind="create_app", arguments={}
    )
    result = {"app": {"id": app.id, "name": "Untrusted label"}}
    links = await completion_links(result, **args)
    assert [(x["kind"], x["url"]) for x in links] == [("track", f"/tracks/{track.id}")]
    second = await Track.create(
        title="Second", workspace_id=workspace.id, visibility="inherit"
    )
    await app.connect(second, edge=CONTAINS)
    links = await completion_links(result, **args)
    assert links == [
        {"kind": "app", "id": app.id, "label": app.name, "url": f"/apps/{app.id}"}
    ]


@pytest.mark.asyncio
async def test_one_entry_links_entry_multiple_entries_link_parent_track():
    from app.models.edges import CONTAINS
    from app.models.nodes import Entry

    owner, _, workspace, _, track, entry = await rooted_graph()

    async def links(refs):
        return await completion_links(
            {"object_refs": refs},
            kind="app_command",
            arguments={},
            user_id=owner.id,
            workspace_id=workspace.id,
        )

    refs = [{"kind": "entry", "id": entry.id, "track_id": "forged-parent"}]
    one = await links(refs)
    assert one[0]["url"] == f"/tracks/{track.id}?entry={entry.id}"
    second = await Entry.create(title="Second", track_id=track.id)
    await track.connect(second, edge=CONTAINS)
    many = await links([*refs, {"kind": "entry", "id": second.id}])
    assert len(many) == 1 and many[0]["url"] == f"/tracks/{track.id}"


@pytest.mark.asyncio
async def test_cross_workspace_and_revoked_targets_have_no_links(monkeypatch):
    from app.services import completion_links as service

    owner, _, workspace, app, _, _ = await rooted_graph()
    arguments = dict(kind="update_app", arguments={"app_id": app.id}, user_id=owner.id)
    assert await completion_links({}, workspace_id="other-workspace", **arguments) == []
    monkeypatch.setattr(service, "resolve_role", AsyncMock(return_value=None))
    assert await completion_links({}, workspace_id=workspace.id, **arguments) == []


@pytest.mark.asyncio
async def test_home_and_dashboard_are_specific_destinations(monkeypatch):
    from app.services import completion_links as service

    owner, _, workspace, app, _, _ = await rooted_graph()
    home = await completion_links(
        {"app": {"id": app.id}},
        kind="update_app",
        arguments={"app_id": app.id, "home": {}},
        user_id=owner.id,
        workspace_id=workspace.id,
    )
    assert home[0]["url"] == f"/apps/{app.id}?section=home"
    real_location = service.locate_resource

    async def locate(kind, identifier, **kwargs):
        if kind == "dashboard":
            return SimpleNamespace(
                workspace_id=workspace.id, app_id=app.id, track_id=""
            )
        return await real_location(kind, identifier, **kwargs)

    monkeypatch.setattr(service, "locate_resource", locate)
    monkeypatch.setattr(
        service.Dashboard,
        "get",
        AsyncMock(return_value=SimpleNamespace(name="My dashboard")),
    )
    dash = await completion_links(
        {"id": "dash", "app_id": app.id},
        kind="update_dashboard",
        arguments={},
        user_id=owner.id,
        workspace_id=workspace.id,
    )
    assert dash[0]["url"] == f"/apps/{app.id}?section=dashboards&dashboard_id=dash"


def test_failed_deleted_and_pending_results_never_link_proposed_ids():
    for result in ({"error": "denied"}, {"needs_agent_build": True}, {"skipped": True}):
        assert completion_refs(result, "update_entry", {"entry_id": "proposed"}) == []
    assert completion_refs({}, "delete_entry", {"entry_id": "gone"}) == []


def test_link_append_preserves_prose_and_escapes_labels_without_duplicate_links():
    link = {"url": "/tracks/t", "label": "Tasks [now]"}
    text = append_completion_links("Updated your tasks.", [link])
    assert text == "Updated your tasks.\n\n[Tasks \\[now\\]](/tracks/t)"
    assert append_completion_links(text, [link]) == text


def test_multiple_entry_completion_removes_individual_shortcuts_and_keeps_track():
    text = "Updated [One](/tracks/t?entry=e1) and [Two](/entries/e2)."
    result = append_completion_links(
        text, [{"label": "Tasks", "url": "/tracks/t"}], {"e1", "e2"}
    )
    assert result == "Updated One and Two.\n\n[Tasks](/tracks/t)"


def test_single_track_app_name_links_to_track_without_duplicate_destination():
    link = {"label": "Tasks", "url": "/tracks/t", "source_url": "/apps/a"}
    assert (
        append_completion_links("Updated [Todo](/apps/a).", [link])
        == "Updated [Todo](/tracks/t)."
    )


@pytest.mark.asyncio
async def test_successful_bulk_acknowledgement_links_the_canonical_track():
    from app.models.edges import CONTAINS
    from app.models.nodes import Entry

    owner, _, workspace, _, track, entry = await rooted_graph()
    second = await Entry.create(title="Second", track_id=track.id)
    await track.connect(second, edge=CONTAINS)
    args = dict(
        kind="bulk_update_entries",
        arguments={"entry_ids": [entry.id, second.id]},
        user_id=owner.id,
        workspace_id=workspace.id,
    )
    links = await completion_links({"updated": 2, "total": 2}, **args)
    assert len(links) == 1 and links[0]["url"] == f"/tracks/{track.id}"
    assert await completion_links({"updated": 1, "total": 2}, **args) == []
