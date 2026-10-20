"""Tag additions retain existing tags, including legacy materialized taxonomy."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.errors import ResourceConflictError
from app.models.nodes import Tag
from app.services.tag_service import assert_unique_tag_name


@pytest.mark.asyncio
async def test_legacy_tag_without_fold_cannot_be_duplicated(monkeypatch):
    tag = SimpleNamespace(id="existing", name="Résumé", color="#123456")
    find = AsyncMock(side_effect=[[], [tag]])
    monkeypatch.setattr(Tag, "find", find)
    with pytest.raises(ResourceConflictError, match="already exists") as error:
        await assert_unique_tag_name(name="resume", track_id="track")
    assert error.value.error_code == "resource.duplicate_tag"
    assert tag.color == "#123456"
    assert find.call_args_list[1].args[0]["context.track_id"] == "track"


@pytest.mark.asyncio
async def test_taxonomy_seed_preserves_live_tag_identity_and_color(monkeypatch):
    from app.services.operational_model_graph import seed_taxonomy_for_track

    original = SimpleNamespace(id="old", name="Existing", color="#123456")
    created = SimpleNamespace(id="new")
    monkeypatch.setattr(Tag, "find", AsyncMock(return_value=[original]))
    create = AsyncMock(return_value=created)
    monkeypatch.setattr(Tag, "create", create)
    connect = AsyncMock()
    await seed_taxonomy_for_track(
        track=SimpleNamespace(id="track"),
        operational_model=SimpleNamespace(connect=connect),
        runtime_tier={
            "taxonomy": {
                "tag_groups": [
                    {
                        "key": "default",
                        "tags": [
                            {"name": "EXISTING", "color": "#ffffff"},
                            {"name": "Added", "color": "#445566"},
                        ],
                    }
                ]
            }
        },
    )
    create.assert_awaited_once()
    assert create.call_args.kwargs["name_fold"] == "added"
    assert create.call_args.kwargs["color"] == "#445566"
    assert original.id == "old" and original.color == "#123456"
