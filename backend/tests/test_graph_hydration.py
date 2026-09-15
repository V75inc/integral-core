"""Tests for graph_hydration batch helpers."""

from __future__ import annotations

import pytest

from app.models.nodes import Tag
from app.services.graph_hydration import batch_get_by_ids


@pytest.mark.asyncio
async def test_batch_get_by_ids_empty():
    assert await batch_get_by_ids(Tag, []) == {}


@pytest.mark.asyncio
async def test_batch_get_by_ids_dedupes():
    from app.utils.time import utc_now_iso

    now = utc_now_iso()
    tag = await Tag.create(
        name="batch-hydration-tag",
        color="#000000",
        track_id="",
        created_at=now,
    )
    result = await batch_get_by_ids(Tag, [tag.id, tag.id])
    assert set(result.keys()) == {tag.id}
    assert result[tag.id].name == "batch-hydration-tag"
