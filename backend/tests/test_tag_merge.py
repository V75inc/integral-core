"""Safe tag-merge preview contract."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.models.edges import CONTAINS, HAS_OPERATIONAL_MODEL, TAGGED_WITH
from app.models.nodes import App, Entry, OperationalModel, Tag, Track
from app.services import tag_merge


@pytest.mark.asyncio
async def test_prepare_tag_merge_returns_value_free_revision_bound_preview(monkeypatch):
    source = SimpleNamespace(
        id="source",
        track_id="track-1",
        app_id="",
        group_key="priority",
        parent_tag_id=None,
        applies_to_entry_types=["task"],
        record_revision=2,
    )
    target = SimpleNamespace(**{**source.__dict__, "id": "target"})
    entry = SimpleNamespace(
        id="entry-1", tags=["source"], track_id="track-1", record_revision=7
    )

    async def tag_get(tag_id: str):
        return {"source": source, "target": target}.get(tag_id)

    async def tag_find(_query):
        return []

    async def tag_nodes_page(**_kwargs):
        return [entry], None

    source.nodes_page = tag_nodes_page
    track = SimpleNamespace(id="track-1", workspace_id="workspace-1")

    async def track_get(_track_id: str):
        return track

    async def allow(**_kwargs):
        return SimpleNamespace(allowed=True)

    monkeypatch.setattr(tag_merge.Tag, "get", tag_get)
    monkeypatch.setattr(tag_merge.Tag, "find", tag_find)
    monkeypatch.setattr(tag_merge.Track, "get", track_get)
    monkeypatch.setattr(tag_merge, "policy_evaluate", allow)

    result = await tag_merge.prepare_tag_merge(
        user_id="user-1",
        source_tag_id="source",
        target_tag_id="target",
        workspace_id="workspace-1",
    )
    assert result["affected_count"] == 1
    assert result["entry_ids"] == ["entry-1"]
    assert len(result["preview_fingerprint"]) == 64
    assert "custom_fields" not in result
    assert result["snapshot"]["entry_revisions"] == {"entry-1": 7}


@pytest.mark.asyncio
async def test_prepare_tag_merge_refuses_different_taxonomy(monkeypatch):
    source = SimpleNamespace(
        id="source",
        track_id="track-1",
        app_id="",
        group_key="priority",
        parent_tag_id=None,
        applies_to_entry_types=["task"],
    )
    target = SimpleNamespace(
        id="target",
        track_id="track-1",
        app_id="",
        group_key="status",
        parent_tag_id=None,
        applies_to_entry_types=["task"],
    )

    async def tag_get(tag_id: str):
        return {"source": source, "target": target}.get(tag_id)

    monkeypatch.setattr(tag_merge.Tag, "get", tag_get)
    result = await tag_merge.prepare_tag_merge(
        user_id="user-1", source_tag_id="source", target_tag_id="target"
    )
    assert result["error"] == "incompatible_taxonomy"


@pytest.mark.asyncio
async def test_tag_merge_deduplicates_existing_target_membership_atomically(
    monkeypatch,
):
    @asynccontextmanager
    async def transaction():
        yield None

    async def allowed(**_kwargs):
        return SimpleNamespace(allowed=True)

    monkeypatch.setattr(tag_merge, "graph_transaction_available", lambda: True)
    monkeypatch.setattr(tag_merge, "postgres_graph_transaction", transaction)
    monkeypatch.setattr(tag_merge, "policy_evaluate", allowed)
    app = await App.create(
        name="Tag merge app",
        workspace_id="tag-merge-workspace",
        owner_user_id="tag-user",
    )
    track = await Track.create(
        title="Tag merge track",
        owner_id="tag-user",
        workspace_id=app.workspace_id,
        schema_revision=1,
    )
    await app.connect(track, edge=CONTAINS)
    model = await OperationalModel.create(
        name="Tag merge model",
        scope="track",
        app_id=app.id,
        workspace_id=app.workspace_id,
        manifest={},
    )
    await track.connect(model, edge=HAS_OPERATIONAL_MODEL)
    track.attached_operational_model_id = model.id
    await track.save()
    source = await Tag.create(
        name="Old priority",
        track_id=track.id,
        group_key="priority",
        applies_to_entry_types=["task"],
    )
    target = await Tag.create(
        name="Priority",
        track_id=track.id,
        group_key="priority",
        applies_to_entry_types=["task"],
    )
    await model.connect(source, edge=CONTAINS)
    await model.connect(target, edge=CONTAINS)
    entry = await Entry.create(
        title="Already has both tags",
        track_id=track.id,
        tags=[source.id, target.id],
        record_revision=4,
    )
    await track.connect(entry, edge=CONTAINS)
    await entry.connect(
        source, edge=TAGGED_WITH, tagged_at="2026-09-01T10:00:00Z", tagged_by="tag-user"
    )
    await entry.connect(
        target,
        edge=TAGGED_WITH,
        tagged_at="2026-09-02T10:00:00Z",
        tagged_by="other-user",
    )
    preview = await tag_merge.prepare_tag_merge(
        user_id="tag-user",
        source_tag_id=source.id,
        target_tag_id=target.id,
        workspace_id=app.workspace_id,
    )
    assert "error" not in preview, preview

    result = await tag_merge.merge_tags(
        user_id="tag-user",
        payload={
            "source_tag_id": source.id,
            "target_tag_id": target.id,
            "preview_fingerprint": preview["preview_fingerprint"],
        },
        workspace_id=app.workspace_id,
    )

    assert result.get("merged") is True, result
    assert await Tag.get(source.id) is None
    merged = await Entry.get(entry.id)
    assert merged.tags == [target.id]
    assert merged.record_revision == 5
    context = await merged.get_context()
    source_edges = await context.find_edges_between(
        merged.id, source.id, edge_class=TAGGED_WITH
    )
    target_edges = await context.find_edges_between(
        merged.id, target.id, edge_class=TAGGED_WITH
    )
    assert source_edges == []
    assert len(target_edges) == 1
    assert target_edges[0].tagged_at == "2026-09-02T10:00:00Z"
    assert target_edges[0].tagged_by == "other-user"
