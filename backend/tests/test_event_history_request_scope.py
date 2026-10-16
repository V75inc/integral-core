"""HTTP history reads stay bound to the caller's selected workspace."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.models.edges import IS_MEMBER_OF
from app.models.nodes import Workspace
from app.services.app_graph import catalog_workspace


@pytest.mark.asyncio
async def test_event_history_routes_isolate_two_workspaces_for_one_principal(
    authenticated_client: AsyncClient, test_user
):
    """A valid membership in two workspaces must not blend their histories."""
    workspaces = []
    tracks = []
    for name in ("History scope Alpha", "History scope Beta"):
        workspace = await Workspace.create(name=name, kind="organization")
        await catalog_workspace(workspace)
        await test_user.connect(workspace, edge=IS_MEMBER_OF, role="owner")
        workspaces.append(workspace)

        response = await authenticated_client.post(
            "/api/tracks",
            headers={"X-Integral-Scope": f"ws:{workspace.id}"},
            json={"title": f"{name} track"},
        )
        assert response.status_code == 200, response.text
        track_id = response.json()["track"]["id"]
        tracks.append(track_id)
        entry = await authenticated_client.post(
            "/api/entries",
            headers={"X-Integral-Scope": f"ws:{workspace.id}"},
            json={"track_id": track_id, "title": f"{name} entry"},
        )
        assert entry.status_code in (200, 201), entry.text

    history_routes = (("/api/audit-log", "cursor"), ("/api/events", "since"))
    for path, cursor_param in history_routes:
        for index, workspace in enumerate(workspaces):
            response = await authenticated_client.get(
                path,
                headers={"X-Integral-Scope": f"ws:{workspace.id}"},
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["events"]
            visible_scopes = {event["scope"] for event in body["events"]}
            assert f"track:{tracks[index]}" in visible_scopes
            assert f"track:{tracks[1 - index]}" not in visible_scopes

        first_page = await authenticated_client.get(
            path,
            headers={"X-Integral-Scope": f"ws:{workspaces[0].id}"},
            params={"limit": 1},
        )
        assert first_page.status_code == 200, first_page.text
        cursor = first_page.json()["next_cursor"]
        assert cursor
        crossed = await authenticated_client.get(
            path,
            headers={"X-Integral-Scope": f"ws:{workspaces[1].id}"},
            params={cursor_param: cursor, "limit": 1},
        )
        assert crossed.status_code == 400, crossed.text


@pytest.mark.asyncio
async def test_personal_account_history_does_not_follow_org_workspace(
    authenticated_client: AsyncClient, test_user
):
    """Account events belong to Personal history even for multi-workspace users."""
    from app.services.change_event_logger import (
        ChangeEventEnvelope,
        get_change_event_logger,
    )

    organization = await Workspace.create(
        name="Account history organization", kind="organization"
    )
    await catalog_workspace(organization)
    await test_user.connect(organization, edge=IS_MEMBER_OF, role="owner")

    event = ChangeEventEnvelope(
        id="",
        ts="2026-10-16T00:00:00+00:00",
        actor_kind="human",
        actor_id=test_user.id,
        action="user.update",
        resource_type="User",
        resource_id=test_user.id,
        scope=f"user:{test_user.id}",
        before={"id": test_user.id, "display_name": "Before"},
        after={"id": test_user.id, "display_name": "After"},
        details={"section": "profile"},
    )
    await get_change_event_logger().persist(event)
    assert event.id

    for path in ("/api/audit-log", "/api/events"):
        personal = await authenticated_client.get(
            path, params={"scope": f"user:{test_user.id}"}
        )
        assert personal.status_code == 200, personal.text
        assert any(item["id"] == event.id for item in personal.json()["events"])

        organization_history = await authenticated_client.get(
            path,
            params={"scope": f"user:{test_user.id}"},
            headers={"X-Integral-Scope": f"ws:{organization.id}"},
        )
        assert organization_history.status_code == 200, organization_history.text
        assert all(
            item["id"] != event.id for item in organization_history.json()["events"]
        )
