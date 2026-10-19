"""Column removal keeps the live field, boards and records consistent."""

import pytest


async def _board(client, *, default=None, options=None, field_type="select"):
    response = await client.post(
        "/api/tracks", json={"title": "Workflow", "visibility": "private"}
    )
    assert response.status_code == 200, response.text
    track = response.json()["track"]["id"]
    field = {
        "key": "stage",
        "name": "Stage",
        "type": field_type,
        "enum": options or ["todo", "done"],
    }
    if default is not None:
        field["default"] = default
    response = await client.post(
        "/api/entry-types",
        json={
            "track_id": track,
            "name": "Work item",
            "form_schema": {"fields": [field]},
        },
    )
    assert response.status_code == 200, response.text
    entry_type = response.json()["entry_type"]["id"]
    body = {
        "name": "Board",
        "type": "kanban",
        "entry_type_keys": ["work_item"],
        "config": {
            "group_by": "custom_fields.stage",
            "kanban_columns": [
                {"key": value, "label": value} for value in field["enum"]
            ],
        },
    }
    response = await client.post(f"/api/tracks/{track}/views", json=body)
    assert response.status_code == 200, response.text
    return track, entry_type, response.json()["view"]["id"], body


@pytest.mark.asyncio
async def test_remove_empty_column_updates_schema_and_survives_readback(
    authenticated_client,
):
    client = authenticated_client
    track, type_id, view_id, _ = await _board(client)
    response = await client.delete(f"/api/views/{view_id}/kanban-columns/done")
    assert response.status_code == 200, response.text
    assert response.json()["removed_option"] == "done"
    schema = (await client.get(f"/api/entry-types/{type_id}")).json()["entry_type"][
        "form_schema"
    ]
    assert schema["fields"][0]["enum"] == ["todo"]
    view = (await client.get(f"/api/views/{view_id}")).json()["view"]
    assert [c["key"] for c in view["config"]["kanban_columns"]] == ["todo"]
    # Listing rematerializes the model; it must not resurrect the removed option.
    types = (await client.get("/api/entry-types", params={"track_id": track})).json()[
        "entry_types"
    ]
    assert next(t for t in types if t["id"] == type_id)["form_schema"]["fields"][0][
        "enum"
    ] == ["todo"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field_type,value", [("select", "done"), ("multi_select", ["done"])]
)
async def test_refuses_column_used_by_record_outside_current_view_filter(
    authenticated_client,
    field_type,
    value,
):
    client = authenticated_client
    track, type_id, view_id, _ = await _board(client, field_type=field_type)
    response = await client.post(
        "/api/entries",
        json={
            "track_id": track,
            "type_id": type_id,
            "title": "Hidden work",
            "custom_fields": {"stage": value},
        },
    )
    assert response.status_code == 200, response.text
    response = await client.delete(f"/api/views/{view_id}/kanban-columns/done")
    assert response.status_code == 400, response.text
    assert "Records still use" in response.text
    assert (await client.get(f"/api/entry-types/{type_id}")).json()["entry_type"][
        "form_schema"
    ]["fields"][0]["enum"] == ["todo", "done"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options,default,message",
    [(["todo", "done"], "done", "default"), (["done"], None, "at least one")],
)
async def test_refuses_default_or_last_option(
    authenticated_client, options, default, message
):
    _, _, view_id, _ = await _board(
        authenticated_client, default=default, options=options
    )
    response = await authenticated_client.delete(
        f"/api/views/{view_id}/kanban-columns/done"
    )
    assert response.status_code == 400, response.text
    assert message in response.text


@pytest.mark.asyncio
async def test_removes_option_consistently_from_other_boards(authenticated_client):
    client = authenticated_client
    track, _, view_id, body = await _board(client)
    response = await client.post(
        f"/api/tracks/{track}/views", json={**body, "name": "Other board"}
    )
    assert response.status_code == 200, response.text
    other_id = response.json()["view"]["id"]
    response = await client.delete(f"/api/views/{view_id}/kanban-columns/done")
    assert response.status_code == 200, response.text
    assert set(response.json()["affected_view_ids"]) == {view_id, other_id}
    other = (await client.get(f"/api/views/{other_id}")).json()["view"]
    assert [column["key"] for column in other["config"]["kanban_columns"]] == ["todo"]


@pytest.mark.asyncio
async def test_foreign_principal_cannot_remove_an_option(
    authenticated_client, second_user_client
):
    _, _, view_id, _ = await _board(authenticated_client)
    response = await second_user_client.delete(
        f"/api/views/{view_id}/kanban-columns/done"
    )
    assert response.status_code in {403, 404}, response.text
    original = (await authenticated_client.get(f"/api/views/{view_id}")).json()["view"]
    assert [column["key"] for column in original["config"]["kanban_columns"]] == [
        "todo",
        "done",
    ]
