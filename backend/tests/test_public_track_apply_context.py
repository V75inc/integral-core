import pytest

from app.api.tracks_public_share import _public_opening_apply_snapshot


@pytest.mark.asyncio
async def test_public_opening_apply_snapshot_maps_body_to_description():
    class _Entry:
        id = "n.Entry.opening1"
        title = "Software Engineer"
        body = "Build APIs.\nCollaborate with product."
        custom_fields = {"salary_range": "USD 80–100k", "status": "open"}

    snap = await _public_opening_apply_snapshot(_Entry())
    assert snap["title"] == "Software Engineer"
    assert snap["body"] == "Build APIs.\nCollaborate with product."
    assert snap["description"] == snap["body"]
    assert snap["salary_range"] == "USD 80–100k"
