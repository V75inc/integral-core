"""Regression: Track always inherits workspace_id from its parent App.

Pre-fix bug: ``create_track`` only loaded the parent App (``sp_for_link``)
when ``visibility`` was unset, so passing both ``app_id`` and an
explicit visibility (the seed pattern) silently stamped the track with
the caller's Personal Workspace while the App lived under the org
Workspace. Symptom: CRM space showed under Acme Inc. but its tracks
appeared under the user's Personal workspace.
"""

import pytest
from httpx import AsyncClient

from app.models.nodes import App, Track


def _scope_headers(workspace_id: str) -> dict[str, str]:
    return {"X-Integral-Scope": f"ws:{workspace_id}"}


async def _pin_workspace_scope(client: AsyncClient, workspace_id: str) -> None:
    """Persist active workspace so list/create paths agree with the org under test."""
    scope = await client.put(
        "/api/users/me/scope",
        json={"workspace_id": workspace_id},
    )
    assert scope.status_code == 200, scope.text
    assert scope.json().get("active_workspace_id") == workspace_id


@pytest.mark.asyncio
async def test_track_in_space_inherits_workspace_id_with_explicit_visibility(
    authenticated_client: AsyncClient, test_user
):
    # Create an org-scoped App first.
    org = await authenticated_client.post(
        "/api/workspaces", json={"name": "Inheritance Org"}
    )
    assert org.status_code == 200, org.text
    org_id = org.json()["workspace"]["id"]
    await _pin_workspace_scope(authenticated_client, org_id)
    headers = _scope_headers(org_id)

    sp = await authenticated_client.post(
        "/api/apps",
        headers=headers,
        json={"name": "Inheritance App", "workspace_id": org_id},
    )
    assert sp.status_code == 200, sp.text
    sp_body = sp.json()["app"]
    sp_id = sp_body["id"]
    sp_node = await App.get(sp_id)
    assert sp_node is not None
    parent_workspace_id = (
        sp_node.workspace_id or sp_body.get("workspace_id") or ""
    ).strip()
    assert parent_workspace_id == org_id, (
        f"App must live in org workspace; got node={sp_node.workspace_id!r} "
        f"export={sp_body.get('workspace_id')!r}"
    )

    # Create a track in that App with an EXPLICIT visibility — this
    # triggered the bug: explicit_vis branch skipped sp_for_link and
    # the track defaulted to the caller's Personal Workspace.
    tr = await authenticated_client.post(
        "/api/tracks",
        headers=headers,
        json={
            "title": "Inherited Track",
            "app_id": sp_id,
            "visibility": "private",
        },
    )
    assert tr.status_code == 200, tr.text
    track_body = tr.json()["track"]
    track_node = await Track.get(track_body["id"])
    assert track_node is not None
    assert (
        track_node.workspace_id or track_body.get("workspace_id")
    ) == parent_workspace_id, (
        "Track must inherit parent App's workspace_id regardless of "
        f"explicit visibility; app={parent_workspace_id!r} "
        f"track_node={track_node.workspace_id!r} export={track_body.get('workspace_id')!r}"
    )


@pytest.mark.asyncio
async def test_track_in_personal_space_stays_personal(
    authenticated_client: AsyncClient, test_user
):
    """A track in a Personal-scope App stays in the same Personal workspace."""
    from app.services.personal_workspace import ensure_personal_workspace_for_user_id

    principal_id = getattr(test_user, "user_id", None) or test_user.id
    personal = await ensure_personal_workspace_for_user_id(principal_id)
    assert personal is not None
    await _pin_workspace_scope(authenticated_client, personal.id)
    headers = _scope_headers(personal.id)

    sp = await authenticated_client.post(
        "/api/apps",
        headers=headers,
        json={"name": "Personal Inherit App"},
    )
    assert sp.status_code == 200, sp.text
    sp_body = sp.json()["app"]
    sp_id = sp_body["id"]
    sp_node = await App.get(sp_id)
    assert sp_node is not None
    parent_ws = (sp_node.workspace_id or sp_body.get("workspace_id") or "").strip()
    assert parent_ws == personal.id

    tr = await authenticated_client.post(
        "/api/tracks",
        headers=headers,
        json={
            "title": "Personal Inherit Track",
            "app_id": sp_id,
            "visibility": "private",
        },
    )
    assert tr.status_code == 200, tr.text
    track_body = tr.json()["track"]
    track_node = await Track.get(track_body["id"])
    assert track_node is not None
    assert (track_node.workspace_id or track_body.get("workspace_id")) == parent_ws
