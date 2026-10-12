"""Owner review derives current roles and keeps other people's grant paths private."""

import pytest

from app.models.edges import COLLABORATES_ON
from app.services.resource_access_blocks import set_resource_block
from app.services.share_links import mint_share_link, redeem_share_link
from app.services.sharing import list_access
from tests.test_access_lifecycle import verified_subject
from tests.test_resource_location import graph

pytestmark = pytest.mark.asyncio


async def test_owner_review_reports_strongest_role_and_absolute_block():
    owner, workspace, app, track, _ = await graph()
    recipient = await verified_subject(workspace)
    await recipient.connect(app, edge=COLLABORATES_ON, role="editor")
    await recipient.connect(track, edge=COLLABORATES_ON, role="viewer")
    snapshot = await list_access(owner.id, "track", track.id)
    assert snapshot["complete"] is True
    current = {row["user_id"]: row["role"] for row in snapshot["effective_access"]}
    assert current[recipient.id] == "editor"
    await set_resource_block(
        actor_id=owner.id,
        resource_type="track",
        resource_id=track.id,
        subject_id=recipient.id,
        blocked=True,
    )
    snapshot = await list_access(owner.id, "track", track.id)
    current = {row["user_id"]: row["role"] for row in snapshot["effective_access"]}
    assert current[recipient.id] is None
    assert any(
        row["source"] == "ResourceAccessBlock" and row["state"] == "blocked"
        for row in snapshot["sources"]
    )


async def test_viewer_review_never_releases_people_or_source_inventory():
    owner, workspace, app, _, _ = await graph()
    viewer = await verified_subject(workspace)
    await viewer.connect(app, edge=COLLABORATES_ON, role="viewer")
    snapshot = await list_access(viewer.id, "app", app.id)
    assert snapshot == {
        "resource_type": "app",
        "resource_id": app.id,
        "effective_role": "viewer",
        "links_visible": False,
    }
