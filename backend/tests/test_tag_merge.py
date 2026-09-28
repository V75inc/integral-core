"""Safe tag-merge preview contract."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

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
