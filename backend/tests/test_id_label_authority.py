"""Human-facing label enrichment must preserve current resource read authority."""

import pytest

from app.models.edges import COLLABORATES_ON, CONTAINS, IS_MEMBER_OF
from app.models.nodes import User
from app.services.app_graph import catalog_user
from app.services.id_resolver import _short, humanize_ids, resolve_id_labels
from tests.test_resource_location import graph

pytestmark = pytest.mark.smoke


@pytest.mark.asyncio
async def test_label_lookup_without_recipient_never_discloses_stored_names():
    """Opaque generated IDs cannot authorize a generic graph metadata lookup."""
    _, workspace, app, track, entry = await graph()
    ids = [workspace.id, app.id, track.id, entry.id]
    assert await resolve_id_labels(ids) == {
        identity: _short(identity) for identity in ids
    }


@pytest.mark.asyncio
async def test_current_owned_labels_require_exact_workspace_scope():
    """Allowed labels remain useful while a foreign active scope denies them."""
    owner, workspace, app, track, entry = await graph()
    ids = [app.id, track.id, entry.id]
    labels = await resolve_id_labels(ids, user_id=owner.id, workspace_id=workspace.id)
    assert labels == {app.id: app.name, track.id: track.title, entry.id: entry.title}
    assert await resolve_id_labels(
        ids, user_id=owner.id, workspace_id="foreign-workspace"
    ) == {identity: _short(identity) for identity in ids}


@pytest.mark.asyncio
async def test_private_app_name_denied_in_stream_persistence_and_card_helpers():
    """A denied tool ID must not regain its name during output formatting."""
    from app.agentive.staging import _humanize_card_text
    from app.api.ai_chat import _humanize_text, _humanize_text_parts
    from app.services.chat_streaming import _DeltaHumanizer

    _, workspace, app, _, _ = await graph()
    app.name = "Synthetic private label that must not be released"
    await app.save()
    outsider = await User.create(display_name="Opaque output recipient")
    await catalog_user(outsider)
    text = f"Permission denied for {app.id}."
    expected = f"Permission denied for {_short(app.id)}."
    assert (
        await humanize_ids(text, user_id=outsider.id, workspace_id=workspace.id)
        == expected
    )
    assert (
        await _humanize_text(text, user_id=outsider.id, workspace_id=workspace.id)
        == expected
    )
    assert await _humanize_text_parts(
        [{"type": "text", "text": text}], user_id=outsider.id, workspace_id=workspace.id
    ) == [{"type": "text", "text": expected}]
    assert (
        await _humanize_card_text(text, user_id=outsider.id, workspace_id=workspace.id)
        == expected
    )
    streamer = _DeltaHumanizer(user_id=outsider.id, workspace_id=workspace.id)
    output = (await streamer.feed(text) or "") + (await streamer.flush() or "")
    assert output == expected and app.name not in output


@pytest.mark.asyncio
async def test_labels_recheck_revocation_and_corrupt_containment():
    """A prior successful lookup never authorizes a later formatter lookup."""
    _, workspace, app, _, _ = await graph()
    reader = await User.create(display_name="Label revocation recipient")
    await catalog_user(reader)
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    grant = await reader.connect(app, edge=COLLABORATES_ON, role="viewer")
    assert (await resolve_id_labels([app.id], user_id=reader.id))[app.id] == app.name
    await grant.delete()
    assert (await resolve_id_labels([app.id], user_id=reader.id))[app.id] == _short(
        app.id
    )
    await reader.connect(app, edge=COLLABORATES_ON, role="viewer")
    _, foreign_workspace, _, _, _ = await graph()
    await foreign_workspace.connect(app, edge=CONTAINS)
    assert (await resolve_id_labels([app.id], user_id=reader.id))[app.id] == _short(
        app.id
    )
