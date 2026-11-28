"""Live metadata previews expose literal, authorized schemas without Entry data."""

import json

import pytest

from app.models.edges import (
    COLLABORATES_ON,
    CONTAINS,
    EXCLUDED_FROM,
    HAS_OPERATIONAL_MODEL,
    IS_MEMBER_OF,
    OWNS,
)
from app.models.nodes import EntryType, OperationalModel, Track, User
from app.services.app_graph import catalog_track, catalog_user
from app.services.walkers.resource_schema_preview import app_schema_preview
from tests.test_resource_location import graph

pytestmark = pytest.mark.asyncio


async def schema_graph():
    user, workspace, app, track, entry = await graph()
    models = await track.nodes(
        edge=[HAS_OPERATIONAL_MODEL], node=[OperationalModel], direction="out", limit=2
    )
    if models:
        model = models[0]
    else:
        model = await OperationalModel.create(name="Schema context test")
        await track.connect(model, edge=HAS_OPERATIONAL_MODEL)
    kind = await EntryType.create(
        name="Candidate",
        track_id=track.id,
        form_schema={
            "_manifest_entry_type_key": "candidate-exact",
            "fields": [
                {"key": "department", "type": "relation"},
                {"key": "status", "type": "select"},
            ],
        },
    )
    await model.connect(kind, edge=CONTAINS)
    return user, workspace, app, track, entry, kind


async def test_preview_has_exact_keys_and_never_captures_entry_values():
    user, workspace, app, track, entry, kind = await schema_graph()
    track.purpose = "Available capacity, distinct from incoming requests. " * 5
    await track.save()
    entry.body = "PRIVATE SAMPLE CONTENT NEVER REQUIRED FOR SCHEMA"
    await entry.save()
    preview = await app_schema_preview(
        app_id=app.id, user_id=user.id, workspace_id=workspace.id
    )
    assert preview["tracks"][0]["id"] == track.id
    types = preview["tracks"][0]["entry_types"]
    selected = next(item for item in types if item["key"] == "candidate-exact")
    assert selected["fields"] == {"department": "relation", "status": "select"}
    assert "PRIVATE SAMPLE" not in json.dumps(preview)
    directory = preview["directory"][0]
    assert directory["id"] == track.id and directory["title"] == track.title
    assert directory["purpose"] == track.purpose[:128]
    assert directory["purpose_incomplete"] is True
    assert set(directory["schema_keys"]) == {"department", "status"}
    denied = await User.create(display_name="Schema guest")
    await catalog_user(denied)
    await denied.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    assert (
        await app_schema_preview(
            app_id=app.id, user_id=denied.id, workspace_id=workspace.id
        )
    )["tracks"] == []


async def test_walker_hook_error_cannot_return_successful_partial_metadata(monkeypatch):
    from app.api.errors import QueryUnavailableError
    from app.services.walkers.resource_schema_preview import ResourceSchemaPreviewWalker

    user, workspace, app, _, _, _ = await schema_graph()

    async def unavailable(*args, **kwargs):
        raise RuntimeError("source unavailable")

    monkeypatch.setattr(ResourceSchemaPreviewWalker, "_queue_types", unavailable)
    with pytest.raises(QueryUnavailableError):
        await app_schema_preview(
            app_id=app.id, user_id=user.id, workspace_id=workspace.id
        )


async def test_shared_template_previews_only_materialized_types_per_track():
    """Shared template siblings retain separate readable type bindings."""
    user, workspace, app, track, _, kind = await schema_graph()
    model = (await track.nodes(edge=[HAS_OPERATIONAL_MODEL], node=[OperationalModel]))[
        0
    ]
    model.app_id = app.id
    model.workspace_id = workspace.id
    model.manifest = {
        "materialization": {
            "kind": "space_track_template",
            "app_id": app.id,
            "template_key": "project-detail",
        },
        "track": {"entry_types": [{"key": "candidate-exact"}]},
    }
    await model.save()
    await app.connect(model, edge=CONTAINS)
    track.template_id = "project-detail"
    await track.save()
    sibling = await Track.create(
        title="Another project",
        workspace_id=workspace.id,
        template_id="project-detail",
        attached_operational_model_id=model.id,
    )
    await catalog_track(sibling)
    await app.connect(sibling, edge=CONTAINS)
    await user.connect(sibling, edge=OWNS)
    await sibling.connect(model, edge=HAS_OPERATIONAL_MODEL)
    blueprint = await EntryType.create(name="Candidate template", is_template=True)
    await model.connect(blueprint, edge=CONTAINS)
    sibling_type = await EntryType.create(
        name="Candidate",
        track_id=sibling.id,
        form_schema={
            "_manifest_entry_type_key": "candidate-exact",
            "fields": [
                {"key": "sibling_only", "type": "text"},
            ],
        },
    )
    await model.connect(sibling_type, edge=CONTAINS)
    preview = await app_schema_preview(
        app_id=app.id, user_id=user.id, workspace_id=workspace.id
    )
    by_track = {row["id"]: row for row in preview["tracks"]}
    assert kind.id in {row["id"] for row in by_track[track.id]["entry_types"]}
    assert sibling_type.id not in {
        row["id"] for row in by_track[track.id]["entry_types"]
    }
    assert {row["id"] for row in by_track[sibling.id]["entry_types"]} == {
        sibling_type.id
    }
    assert blueprint.id not in json.dumps(preview)
    reader = await User.create(display_name="Restricted project reader")
    await catalog_user(reader)
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    await reader.connect(app, edge=COLLABORATES_ON, role="viewer")
    await reader.connect(sibling, edge=EXCLUDED_FROM)
    restricted = await app_schema_preview(
        app_id=app.id, user_id=reader.id, workspace_id=workspace.id
    )
    assert restricted["tracks"]
    assert sibling.id not in json.dumps(restricted)
    assert "sibling_only" not in json.dumps(restricted)
    assert kind.id in json.dumps(restricted)

    model.manifest["track"]["entry_types"].append({"key": "unmaterialized"})
    await model.save()
    partial = await app_schema_preview(
        app_id=app.id, user_id=user.id, workspace_id=workspace.id
    )
    assert partial["incomplete"]
    assert all(item["schema_keys_incomplete"] for item in partial["directory"])
    from app.api.errors import QueryUnavailableError

    sibling.template_id = "different-template"
    await sibling.save()
    with pytest.raises(QueryUnavailableError):
        await app_schema_preview(
            app_id=app.id, user_id=user.id, workspace_id=workspace.id
        )


async def test_dedicated_model_still_refuses_inconsistent_type_binding():
    """The template exception cannot mask corrupt dedicated bindings."""
    from app.api.errors import QueryUnavailableError

    user, workspace, app, _, _, kind = await schema_graph()
    kind.track_id = "different-track"
    await kind.save()
    with pytest.raises(QueryUnavailableError):
        await app_schema_preview(
            app_id=app.id, user_id=user.id, workspace_id=workspace.id
        )


async def test_relevant_literal_keys_survive_a_wide_schema_and_directory_budget():
    user, workspace, app, track, entry, kind = await schema_graph()
    kind.form_schema["fields"] = [
        {"key": f"unrelated_{i}", "type": "text"} for i in range(30)
    ] + [{"key": "available_capacity", "label": "Available capacity", "type": "number"}]
    await kind.save()
    entry.body = "PRIVATE RECORD VALUE"
    await entry.save()
    preview = await app_schema_preview(
        app_id=app.id,
        user_id=user.id,
        workspace_id=workspace.id,
        query="How much capacity is available?",
    )
    kind_preview = next(
        item
        for item in preview["tracks"][0]["entry_types"]
        if item["key"] == "candidate-exact"
    )
    assert "available_capacity" in kind_preview["fields"]
    directory = preview["directory"][0]
    assert "available_capacity" in directory["schema_keys"]
    assert len(json.dumps(directory["schema_keys"], ensure_ascii=False).encode()) <= 96
    assert directory["schema_keys_incomplete"]
    assert "PRIVATE RECORD VALUE" not in json.dumps(preview)
