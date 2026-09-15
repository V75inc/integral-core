"""``GET /entries/{id}/attachments`` returns a defined order.

The handler returned whatever ``entry.nodes(edge=["HAS_ATTACHMENT"])`` yielded
— graph-traversal order, which is not an order at all. Observed live with four
attachments on one entry: the response matched neither ascending nor
descending ``created_at``, and traversal order can differ between loads.

That is visible to a user twice over:

1. The attachments list reshuffles for no reason the reader can see.
2. The entry's hero preview is "the first image attachment"
   (``firstImageAttachmentFromAttachments`` — "first image in list order"), so
   WHICH image represents the entry could change on reload without anyone
   touching the entry.

Newest first, tie-broken by id. The tie-break is not decoration: a multi-file
upload writes several attachments inside the same millisecond, and without it
those rows are free to swap places between requests — the same defect in
miniature.
"""

from __future__ import annotations

import pytest

# Imported at module scope on purpose: importing the auth service loads the
# app config, which populates os.environ. Deferred to inside a test, that shows
# up as an environment leak to the autouse guard in conftest.
from app.api.auth import _get_auth_service
from app.models.edges import HAS_ATTACHMENT
from app.models.nodes import Attachment, Entry, Track

pytestmark = pytest.mark.smoke


async def _entry_with_attachments(created_ats: list[str]) -> tuple[str, str]:
    """An entry carrying attachments created at the given timestamps.

    Inserted in a deliberately jumbled order so a handler that simply echoes
    traversal order cannot pass by luck. Returns the entry id plus an auth
    principal that may read it — the handler runs an ``entry.read`` policy
    check, so an unauthorised caller gets an error envelope rather than a
    list, and the ordering assertion would never run.
    """
    from jvspatial.api.auth.models import UserCreate

    from app.models.edges import CONTAINS, IS_MEMBER_OF, OWNS
    from app.models.nodes import App, User, Workspace

    # Fully edge-wired to a rooted parent (I-GRAPH-01).
    ws = await Workspace.create(
        kind="organization", name="Ordering", name_fold="ordering"
    )
    app_node = await App.create(
        name="Ordering", name_fold="ordering", workspace_id=ws.id
    )
    track = await Track.create(
        title="Ordering", title_fold="ordering", workspace_id=ws.id
    )
    entry = await Entry.create(title="Ordered attachments", track_id=track.id)
    await ws.connect(app_node, edge=CONTAINS)
    await app_node.connect(track, edge=CONTAINS)
    await track.connect(entry, edge=CONTAINS)

    resp = await _get_auth_service().register_user(
        UserCreate(email="attach-order@example.com", password="testpassword123")
    )
    owner = await User.create(user_id=resp.id, display_name="Owner")
    # Workspace membership AND a track grant: an org workspace gates everything
    # inside it, so a track grant alone resolves to no access at all.
    await owner.connect(ws, edge=IS_MEMBER_OF, role="admin")
    await owner.connect(track, edge=OWNS)

    ids: list[str] = []
    for i, created in enumerate(created_ats):
        att = await Attachment.create(
            filename=f"file-{i}.png",
            mime_type="image/png",
            size=10,
            storage_key=f"k{i}",
            created_at=created,
        )
        await entry.connect(att, edge=HAS_ATTACHMENT)
        ids.append(att.id)
    return entry.id, resp.id


@pytest.mark.asyncio
async def test_attachments_come_back_newest_first():
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.attachments import list_entry_attachments

    entry_id, principal = await _entry_with_attachments(
        [
            "2026-08-12T10:00:00+00:00",  # oldest
            "2026-08-12T12:00:00+00:00",  # newest
            "2026-08-12T11:00:00+00:00",  # middle
        ]
    )

    result = await invoke_route_in_process(
        list_entry_attachments,
        principal_id=principal,
        entry_id=entry_id,
    )
    assert "attachments" in result, result
    stamps = [a["created_at"] for a in result["attachments"]]

    assert stamps == sorted(stamps, reverse=True), (
        "attachments are not newest-first — the list reshuffles between loads "
        "and the hero preview can silently change which image it features"
    )
    # Guard the guard: a single-item response would satisfy the assertion above
    # while proving nothing about ordering.
    assert len(stamps) == 3


@pytest.mark.asyncio
async def test_identical_timestamps_still_have_a_total_order():
    """A multi-file upload stamps several rows in the same millisecond."""
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.attachments import list_entry_attachments

    same = "2026-08-12T10:00:00+00:00"
    entry_id, principal = await _entry_with_attachments([same, same, same])

    async def ids_now() -> list[str]:
        result = await invoke_route_in_process(
            list_entry_attachments,
            principal_id=principal,
            entry_id=entry_id,
        )
        assert "attachments" in result, result
        return [a["id"] for a in result["attachments"]]

    first = await ids_now()
    assert len(first) == 3
    # Stable across repeated reads, and descending by id — without the
    # tie-break these three are free to permute between requests.
    assert first == await ids_now()
    assert first == sorted(first, reverse=True)
