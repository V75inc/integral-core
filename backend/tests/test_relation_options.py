"""Picker correctness through the real scope and audience pipeline."""

import uuid

import pytest


async def _source(client, app_id=None, cross=False):
    response = await client.post(
        "/api/tracks",
        json={
            "title": f"Picker fixture {uuid.uuid4().hex[:8]}",
            "visibility": "private",
            **({"app_id": app_id} if app_id else {}),
        },
    )
    assert response.status_code == 200, response.text
    track_id = response.json()["track"]["id"]
    response = await client.post(
        "/api/entry-types",
        json={
            "track_id": track_id,
            "name": "Record",
            "form_schema": {
                "fields": [
                    {
                        "key": "related",
                        "name": "Related",
                        "type": "relation",
                        "relation": {"many": False, "allow_cross_track": cross},
                    },
                ]
            },
        },
    )
    assert response.status_code == 200, response.text
    type_id = response.json()["entry_type"]["id"]
    return dict(track_id=track_id, entry_type_id=type_id, field_key="related")


@pytest.mark.asyncio
async def test_picker_pages_searches_and_avoids_enrichment(
    authenticated_client, monkeypatch
):
    source = await _source(authenticated_client)
    for i in range(7):
        result = await authenticated_client.post(
            "/api/entries",
            json={
                "track_id": source["track_id"],
                "type_id": source["entry_type_id"],
                "title": f"Option {i}",
            },
        )
        assert result.status_code == 200, result.text

    async def forbidden(*args, **kwargs):
        raise AssertionError("Picker called full list enrichment")

    monkeypatch.setattr("app.api.entries.enrich_entry_page_for_response", forbidden)
    ids = []
    cursor = None
    while True:
        result = await authenticated_client.get(
            "/api/relation-options",
            params={**source, "limit": 2, **({"cursor": cursor} if cursor else {})},
        )
        assert result.status_code == 200, result.text
        body = result.json()
        ids.extend(option["id"] for option in body["options"])
        assert all(
            set(option) == {"id", "label", "track_id", "track_title"}
            for option in body["options"]
        )
        if not body["has_more"]:
            break
        assert body["next_cursor"] and body["next_cursor"] != cursor
        cursor = body["next_cursor"]
    assert len(ids) == len(set(ids)) == 7
    result = await authenticated_client.get(
        "/api/relation-options", params={**source, "q": "Option 6"}
    )
    assert result.status_code == 200, result.text
    assert [item["label"] for item in result.json()["options"]] == ["Option 6"]


@pytest.mark.asyncio
async def test_picker_rejects_wrong_schema_scope_and_invalid_limit(
    authenticated_client,
):
    source = await _source(authenticated_client)
    for changes in (
        {"field_key": "unknown"},
        {"entry_type_id": "n.EntryType.missing"},
        {"limit": 51},
        {"q": "x" * 201},
        {"entry_id": "n.Entry.missing"},
    ):
        result = await authenticated_client.get(
            "/api/relation-options", params={**source, **changes}
        )
        assert result.status_code in {400, 404, 422}, result.text
    result = await authenticated_client.get(
        "/api/relation-options",
        params=source,
        headers={"X-Integral-Scope": "ws:n.Workspace.foreign"},
    )
    assert result.status_code == 403, result.text


@pytest.mark.asyncio
async def test_picker_rechecks_source_permission_and_foreign_source_entries(
    authenticated_client, monkeypatch
):
    source = await _source(authenticated_client)
    created = await authenticated_client.post(
        "/api/entries",
        json={
            "track_id": source["track_id"],
            "type_id": source["entry_type_id"],
            "title": "Visible",
        },
    )
    visible = created.json()["entry"]["id"]
    created = await authenticated_client.post(
        "/api/entries",
        json={
            "track_id": source["track_id"],
            "type_id": source["entry_type_id"],
            "title": "Withheld",
        },
    )
    # A current source denial must stop before any candidate data is exposed.
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import app.services.relation_options as service

    original = service.evaluate
    monkeypatch.setattr(
        service, "evaluate", AsyncMock(return_value=SimpleNamespace(allowed=False))
    )
    response = await authenticated_client.get("/api/relation-options", params=source)
    assert response.status_code == 403, response.text
    assert "Visible" not in response.text and "Withheld" not in response.text
    monkeypatch.setattr(service, "evaluate", original)
    foreign = await _source(authenticated_client)
    response = await authenticated_client.get(
        "/api/relation-options", params={**foreign, "entry_id": visible}
    )
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_picker_does_not_expand_into_an_undeclared_app(authenticated_client):
    sources = []
    targets = []
    for name in ["Source pool", "Other pool"]:
        result = await authenticated_client.post("/api/apps", json={"name": name})
        assert result.status_code == 200, result.text
        app_id = result.json()["app"]["id"]
        source = await _source(authenticated_client, app_id=app_id, cross=True)
        sources.append(source)
        created = await authenticated_client.post(
            "/api/entries",
            json={
                "track_id": source["track_id"],
                "type_id": source["entry_type_id"],
                "title": name,
            },
        )
        assert created.status_code == 200, created.text
        targets.append(created.json()["entry"]["id"])
    result = await authenticated_client.get("/api/relation-options", params=sources[0])
    assert result.status_code == 200, result.text
    assert [option["id"] for option in result.json()["options"]] == [targets[0]]


@pytest.mark.asyncio
async def test_picker_omits_currently_excluded_targets_and_rechecks_access(
    authenticated_client,
    test_user,
):
    from app.models.edges import COLLABORATES_ON, EXCLUDED_FROM
    from app.models.nodes import Entry, Track
    from app.schemas.relation_options import RelationOptionsRequest
    from app.services.permissions import get_user_node
    from app.services.relation_options import relation_options
    from tests.fixtures.workspaces import make_org_workspace
    from tests.test_entry_projection_authority import reader_in

    workspace = await make_org_workspace(
        "Picker admission fixture", owner=await get_user_node(test_user.id)
    )
    prior = authenticated_client.headers.get("X-Integral-Scope")
    authenticated_client.headers["X-Integral-Scope"] = f"ws:{workspace.id}"
    try:
        source = await _source(authenticated_client)
        ids = []
        for title in ["Allowed choice", "Excluded choice"]:
            created = await authenticated_client.post(
                "/api/entries",
                json={
                    "track_id": source["track_id"],
                    "type_id": source["entry_type_id"],
                    "title": title,
                },
            )
            assert created.status_code == 200, created.text
            ids.append(created.json()["entry"]["id"])
    finally:
        if prior is None:
            authenticated_client.headers.pop("X-Integral-Scope", None)
        else:
            authenticated_client.headers["X-Integral-Scope"] = prior
    reader = await reader_in(workspace)
    await reader.connect(
        await Track.get(source["track_id"]), edge=COLLABORATES_ON, role="viewer"
    )
    await reader.connect(await Entry.get(ids[1]), edge=EXCLUDED_FROM)
    params = RelationOptionsRequest(**source)
    first = await relation_options(reader.id, workspace.id, params)
    assert [option.id for option in first.options] == [ids[0]]
    assert "Excluded choice" not in first.model_dump_json()
    await reader.connect(await Entry.get(ids[0]), edge=EXCLUDED_FROM)
    second = await relation_options(reader.id, workspace.id, params)
    assert second.options == []


@pytest.mark.asyncio
async def test_picker_direct_entry_grant_does_not_reveal_private_parent(
    authenticated_client, test_user
):
    from app.models.edges import COLLABORATES_ON
    from app.models.nodes import Entry, Track
    from app.schemas.relation_options import RelationOptionsRequest
    from app.services.permissions import get_user_node
    from app.services.relation_options import relation_options
    from tests.fixtures.workspaces import make_org_workspace
    from tests.test_entry_projection_authority import reader_in

    workspace = await make_org_workspace(
        "Picker private parent fixture", owner=await get_user_node(test_user.id)
    )
    prior = authenticated_client.headers.get("X-Integral-Scope")
    authenticated_client.headers["X-Integral-Scope"] = f"ws:{workspace.id}"
    try:
        response = await authenticated_client.post("/api/apps", json={"name": "Picker"})
        assert response.status_code == 200, response.text
        app_id = response.json()["app"]["id"]
        source = await _source(authenticated_client, app_id=app_id, cross=True)
        target = await _source(authenticated_client, app_id=app_id)
        response = await authenticated_client.post(
            "/api/entries",
            json={
                "track_id": target["track_id"],
                "type_id": target["entry_type_id"],
                "title": "Granted child",
            },
        )
        assert response.status_code == 200, response.text
        entry = await Entry.get(response.json()["entry"]["id"])
    finally:
        if prior is None:
            authenticated_client.headers.pop("X-Integral-Scope", None)
        else:
            authenticated_client.headers["X-Integral-Scope"] = prior
    reader = await reader_in(workspace)
    await reader.connect(
        await Track.get(source["track_id"]), edge=COLLABORATES_ON, role="viewer"
    )
    await reader.connect(entry, edge=COLLABORATES_ON, role="viewer")
    result = await relation_options(
        reader.id, workspace.id, RelationOptionsRequest(**source)
    )
    assert [
        (option.id, option.label, option.track_title) for option in result.options
    ] == [(entry.id, "Granted child", "")]
