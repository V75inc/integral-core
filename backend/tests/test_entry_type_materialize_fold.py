"""materialize_entry_types_from_tier sets name_fold on create."""

from __future__ import annotations

import pytest

from app.api.validators_common import compute_fold

pytestmark = pytest.mark.smoke


@pytest.mark.asyncio
async def test_materialize_sets_name_fold(test_user, monkeypatch):
    """Materialized EntryType nodes carry name_fold for uniqueness."""
    from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
    from app.models.nodes import EntryType, Track, Workspace
    from app.services.app_graph import ensure_track_attached_content_profile
    from app.services.entry_type_service import materialize_entry_types_from_tier
    from app.utils.time import utc_now_iso

    now = utc_now_iso()
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Fold Materialize WS",
        name_fold="fold materialize ws",
        created_at=now,
        updated_at=now,
    )
    await test_user.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)
    track = await Track.create(
        title="Fold Track",
        owner_id=test_user.id,
        workspace_id=ws.id,
        visibility="private",
    )
    await test_user.connect(track, edge=COLLABORATES_ON, role="owner", added_at=now)
    await ensure_track_attached_content_profile(track)

    async def fake_resolve(_track):
        return (
            None,
            {
                "entry_types": [
                    {"name": "Bug Report", "key": "bug_report", "icon": "bug"},
                ]
            },
            None,
        )

    monkeypatch.setattr(
        "app.services.entry_type_service.resolve_track_runtime_profile",
        fake_resolve,
    )

    out = await materialize_entry_types_from_tier(track)
    assert len(out) == 1
    assert out[0].name == "Bug Report"
    assert out[0].name_fold == compute_fold("Bug Report")

    # Idempotent: second call returns the same node, does not duplicate.
    again = await materialize_entry_types_from_tier(track)
    assert len(again) == 1
    assert again[0].id == out[0].id
    found = await EntryType.find(
        {
            "context.track_id": track.id,
            "context.name_fold": compute_fold("Bug Report"),
        }
    )
    assert len(list(found or [])) == 1
