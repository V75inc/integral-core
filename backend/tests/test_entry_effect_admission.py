"""Supported shared Entry commands deny current unauthorized effects themselves."""

import pytest
from jvspatial.core.context import get_default_context

from app.api.errors import InsufficientPermissionsError
from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
from app.models.nodes import Entry, User, Workspace
from app.services.app_graph import catalog_user
from app.services.entry_create import create_entry_in_track
from app.services.entry_deletion import delete_entry_fast
from app.services.entry_update import update_entry_in_track
from tests.test_entry_create_type_id import _bootstrap_track_with_type

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]


async def test_reader_cannot_use_shared_create_update_or_delete_services():
    owner, track, entry_type = await _bootstrap_track_with_type(
        "effect-service@example.com"
    )
    entry = await create_entry_in_track(
        track=track, user_id=owner, type_id=entry_type.id, title="Retained content"
    )
    reader = await User.create(display_name="Read-only effect caller")
    await catalog_user(reader)
    await reader.connect(
        await Workspace.get(track.workspace_id), edge=IS_MEMBER_OF, role="member"
    )
    await reader.connect(track, edge=COLLABORATES_ON, role="viewer")
    with pytest.raises(InsufficientPermissionsError):
        await create_entry_in_track(
            track=track,
            user_id=reader.id,
            type_id=entry_type.id,
            title="Denied service create",
        )
    with pytest.raises(InsufficientPermissionsError):
        await update_entry_in_track(
            entry_id=entry.id, user_id=reader.id, title="Denied update"
        )
    with pytest.raises(InsufficientPermissionsError):
        await delete_entry_fast(entry, actor_user_id=reader.id)
    stored = await get_default_context().database.get("node", entry.id)
    assert stored["context"]["title"] == "Retained content"
    assert await Entry.find({"context.title": "Denied service create"}) == []
    await update_entry_in_track(
        entry_id=entry.id, user_id=owner, title="Permitted owner update"
    )
    assert (await Entry.find_one({"id": entry.id})).title == "Permitted owner update"
    await delete_entry_fast(entry, actor_user_id=owner)
    assert await get_default_context().database.get("node", entry.id) is None
