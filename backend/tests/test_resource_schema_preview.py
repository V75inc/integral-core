"""Live metadata previews expose literal, authorized schemas without Entry data."""

import json

import pytest

from app.models.edges import CONTAINS, HAS_OPERATIONAL_MODEL, IS_MEMBER_OF
from app.models.nodes import EntryType, OperationalModel, User
from app.services.app_graph import catalog_user
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
