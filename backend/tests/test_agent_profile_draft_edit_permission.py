"""Draft edit authority follows named parent links, never scalar fallback."""

import pytest

from app.models.edges import HAS_DRAFT_PROFILE
from app.services.model_authority import get_current_model
from app.services.operational_model_atomic_swap import fork_draft
from app.services.operational_model_authoring import _user_can_edit_cp
from tests.test_resource_location import graph

pytestmark = pytest.mark.smoke


@pytest.mark.asyncio
async def test_draft_cp_edit_resolves_via_published_parent():
    owner, _, _, track, _ = await graph()
    published = await get_current_model(track.attached_operational_model_id)
    draft = await fork_draft(published=published, actor_id=owner.id)
    assert await _user_can_edit_cp(user_id=owner.id, cp=draft)
    context = await draft.get_context()
    links = await context.find_edges_between(
        published.id, draft.id, edge_class=HAS_DRAFT_PROFILE
    )
    assert len(links) == 1
    await links[0].delete()
    # The warmed draft still claims draft_of_id, but the source link is gone.
    assert not await _user_can_edit_cp(user_id=owner.id, cp=draft)


@pytest.mark.asyncio
async def test_orphan_cp_without_parent_is_denied():
    from types import SimpleNamespace

    owner, _, _, _, _ = await graph()
    orphan = SimpleNamespace(
        id="n.OperationalModel.missing", scope="track", draft_of_id=None
    )
    assert not await _user_can_edit_cp(user_id=owner.id, cp=orphan)
