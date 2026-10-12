"""Shared editors retain their own relation authority through HTTP routes.

These use signed test-user JWTs and the in-process test auth adapter. They
qualify route/actor propagation and storage, not installed browser sessions,
account disabling, independent workers, or provider/native execution.
"""

import pytest

from app.models.edges import CONTAINS, IS_MEMBER_OF, OWNS, REFERENCES
from app.models.nodes import App, Entry, EntryType, Track, Workspace
from app.services.app_graph import catalog_app, catalog_track, catalog_workspace
from app.services.permissions import can_edit_entry
from app.services.sharing import add_collaborator, remove_collaborator

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]


async def _fixture(editor, owner, many):
    from app.services.entry_create import create_entry_in_track

    workspace = await Workspace.create(
        name="HTTP relation admission", kind="organization"
    )
    await catalog_workspace(workspace)
    await owner.connect(workspace, edge=IS_MEMBER_OF, role="owner")
    await editor.connect(workspace, edge=IS_MEMBER_OF, role="member")
    apps, tracks, types = {}, {}, {}
    for name in ("consumer", "provider", "other"):
        app = await App.create(
            name=name, workspace_id=workspace.id, owner_user_id=owner.user_id
        )
        await catalog_app(app)
        await workspace.connect(app, edge=CONTAINS)
        await owner.connect(app, edge=OWNS)
        track = await Track.create(
            title=name,
            template_id="source" if name == "consumer" else "targets",
            owner_id=owner.user_id,
            workspace_id=workspace.id,
            visibility="inherit" if name == "consumer" else "private",
        )
        await catalog_track(track)
        await app.connect(track, edge=CONTAINS)
        await owner.connect(track, edge=OWNS)
        entry_type = await EntryType.create(
            name="Source Record" if name == "consumer" else "Target Record",
            track_id=track.id,
            form_schema={
                "fields": (
                    [
                        {
                            "key": "related",
                            "type": "relation",
                            "relation": {
                                "target": "entry",
                                "many": many,
                                "allow_cross_track": True,
                                "allow_cross_app": True,
                                "target_app": "provider",
                                "target_entry_types": ["target_record"],
                                "target_track_types": ["targets"],
                            },
                        }
                    ]
                    if name == "consumer"
                    else []
                )
            },
        )
        await track.connect(entry_type, edge=CONTAINS)
        apps[name], tracks[name], types[name] = app, track, entry_type
    targets = []
    for name in ("provider", "provider", "other"):
        target = await Entry.create(
            title=f"Private HTTP target {len(targets)}",
            track_id=tracks[name].id,
            type_id=types[name].id,
            author_id=owner.user_id,
        )
        await tracks[name].connect(target, edge=CONTAINS)
        await owner.connect(target, edge=OWNS)
        targets.append(target)
    source = await create_entry_in_track(
        track=tracks["consumer"],
        entry_type=types["consumer"],
        user_id=owner.user_id,
        workspace_id=workspace.id,
        title="Shared HTTP source",
        custom_fields={},
        skip_profanity=True,
    )
    await add_collaborator(
        owner.user_id, "app", apps["consumer"].id, editor.id, "editor"
    )
    assert source.author_id != editor.user_id
    assert await can_edit_entry(editor.user_id, source.id)
    return workspace, apps, tracks, types, source, targets


def _value(target, many):
    return [target.id] if many else target.id


async def _snapshot(entry):
    graph = await entry.get_context()
    await graph._evict_from_cache(entry.id)
    stored = await Entry.get(entry.id)
    edges = await graph.find_edges_between(entry.id, None, edge_class=REFERENCES)
    return (
        stored.custom_fields,
        stored.record_revision,
        {edge.id: (edge.target, edge.target_app_id) for edge in edges},
    )


async def _assert_editor(client, editor, source, workspace):
    response = await client.get("/api/auth/me")
    assert response.status_code == 200
    user = response.json()["user"]
    assert user["id"] == editor.id
    assert "admin" not in user.get("roles", [])
    response = await client.get(
        f"/api/entries/{source.id}", headers={"X-Integral-Scope": f"ws:{workspace.id}"}
    )
    assert response.status_code == 200


@pytest.mark.parametrize("many", [False, True])
async def test_shared_editor_http_refuses_private_or_mixed_targets(
    many, authenticated_client, test_user, test_user2
):
    from app.services.entry_update import update_entry_in_track

    ws, _, tracks, types, source, targets = await _fixture(test_user, test_user2, many)
    private, allowed, _ = targets
    headers = {"X-Integral-Scope": f"ws:{ws.id}"}
    await _assert_editor(authenticated_client, test_user, source, ws)
    assert (
        await authenticated_client.get(f"/api/entries/{private.id}", headers=headers)
    ).status_code == 403
    await add_collaborator(
        test_user2.user_id, "entry", allowed.id, test_user.id, "viewer"
    )
    await update_entry_in_track(
        entry_id=source.id,
        user_id=test_user2.user_id,
        workspace_id=ws.id,
        custom_fields={"related": _value(allowed, many)},
    )
    before = await _snapshot(source)
    children = await tracks["consumer"].nodes(edge=[CONTAINS], node=["Entry"], limit=10)
    attempted = [allowed.id, private.id] if many else private.id
    response = await authenticated_client.post(
        "/api/entries",
        headers=headers,
        json={
            "track_id": tracks["consumer"].id,
            "type_id": types["consumer"].id,
            "title": "Denied shared create",
            "custom_fields": {"related": attempted},
        },
    )
    assert response.status_code == 400
    response = await authenticated_client.put(
        f"/api/entries/{source.id}",
        headers=headers,
        json={
            "custom_fields": {"related": attempted},
            "expected_record_revision": before[1],
        },
    )
    assert response.status_code == 400
    assert "read access" in response.text
    assert await _snapshot(source) == before
    after = await tracks["consumer"].nodes(edge=[CONTAINS], node=["Entry"], limit=10)
    assert {entry.id for entry in after} == {entry.id for entry in children}


@pytest.mark.parametrize("many", [False, True])
async def test_shared_editor_http_links_authorized_target_and_rechecks_withdrawal(
    many, authenticated_client, test_user, test_user2
):
    ws, apps, tracks, types, source, targets = await _fixture(
        test_user, test_user2, many
    )
    target = targets[0]
    headers = {"X-Integral-Scope": f"ws:{ws.id}"}
    await _assert_editor(authenticated_client, test_user, source, ws)
    await add_collaborator(
        test_user2.user_id, "entry", target.id, test_user.id, "viewer"
    )
    assert (
        await authenticated_client.get(f"/api/entries/{target.id}", headers=headers)
    ).status_code == 200
    before = await _snapshot(source)
    payload = {
        "custom_fields": {"related": _value(target, many)},
        "expected_record_revision": before[1],
    }
    response = await authenticated_client.put(
        f"/api/entries/{source.id}", headers=headers, json=payload
    )
    assert response.status_code == 200
    accepted = await _snapshot(source)
    assert accepted[0]["related"] == _value(target, many)
    assert accepted[1] == before[1] + 1
    assert list(accepted[2].values()) == [(target.id, apps["provider"].id)]
    response = await authenticated_client.post(
        "/api/entries",
        headers=headers,
        json={
            "track_id": tracks["consumer"].id,
            "type_id": types["consumer"].id,
            "title": "Authorized shared create",
            "custom_fields": payload["custom_fields"],
        },
    )
    assert response.status_code == 200
    created = await Entry.get(response.json()["entry"]["id"])
    assert created.author_id == test_user.user_id
    assert (await _snapshot(created))[0]["related"] == _value(target, many)
    await remove_collaborator(test_user2.user_id, "entry", target.id, test_user.id)
    assert (
        await authenticated_client.get(f"/api/entries/{target.id}", headers=headers)
    ).status_code == 403
    payload["expected_record_revision"] = accepted[1]
    response = await authenticated_client.put(
        f"/api/entries/{source.id}", headers=headers, json=payload
    )
    assert response.status_code == 400
    assert "read access" in response.text
    assert await _snapshot(source) == accepted
    response = await authenticated_client.get(
        f"/api/entries/{source.id}", headers=headers
    )
    assert response.status_code == 200
    assert target.title not in response.text


@pytest.mark.parametrize("many", [False, True])
async def test_shared_editor_http_cannot_substitute_a_readable_wrong_app(
    many, authenticated_client, test_user, test_user2
):
    ws, _, _, _, source, targets = await _fixture(test_user, test_user2, many)
    wrong = targets[2]
    headers = {"X-Integral-Scope": f"ws:{ws.id}"}
    await _assert_editor(authenticated_client, test_user, source, ws)
    await add_collaborator(
        test_user2.user_id, "entry", wrong.id, test_user.id, "viewer"
    )
    assert (
        await authenticated_client.get(f"/api/entries/{wrong.id}", headers=headers)
    ).status_code == 200
    before = await _snapshot(source)
    response = await authenticated_client.put(
        f"/api/entries/{source.id}",
        headers=headers,
        json={
            "custom_fields": {"related": _value(wrong, many)},
            "expected_record_revision": before[1],
        },
    )
    assert response.status_code == 400
    assert "declared App" in response.text
    assert await _snapshot(source) == before


async def test_shared_editor_http_cannot_write_after_source_grant_withdrawal(
    authenticated_client, test_user, test_user2
):
    ws, apps, tracks, types, source, targets = await _fixture(
        test_user, test_user2, False
    )
    target = targets[0]
    headers = {"X-Integral-Scope": f"ws:{ws.id}"}
    await _assert_editor(authenticated_client, test_user, source, ws)
    await add_collaborator(
        test_user2.user_id, "entry", target.id, test_user.id, "viewer"
    )
    before = await _snapshot(source)
    await remove_collaborator(
        test_user2.user_id, "app", apps["consumer"].id, test_user.id
    )
    assert (
        await authenticated_client.get(f"/api/entries/{target.id}", headers=headers)
    ).status_code == 200
    response = await authenticated_client.put(
        f"/api/entries/{source.id}",
        headers=headers,
        json={
            "custom_fields": {"related": target.id},
            "expected_record_revision": before[1],
        },
    )
    assert response.status_code == 403
    response = await authenticated_client.post(
        "/api/entries",
        headers=headers,
        json={
            "track_id": tracks["consumer"].id,
            "type_id": types["consumer"].id,
            "title": "Denied withdrawn source",
            "custom_fields": {"related": target.id},
        },
    )
    assert response.status_code == 403
    assert await _snapshot(source) == before
