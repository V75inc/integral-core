"""Regression tests for adversarial review remediations (share links, agent profiles)."""

from __future__ import annotations

import pytest

from app.services.share_links import (
    _is_expired,
    link_intent,
    mint_share_link,
    redeem_share_link,
    validate_expires_at,
)


def test_validate_expires_at_rejects_garbage():
    from app.api.errors import BadRequestError

    with pytest.raises(BadRequestError):
        validate_expires_at("not-a-datetime")


def test_is_expired_treats_unparseable_as_expired():
    from types import SimpleNamespace

    link = SimpleNamespace(expires_at="not-a-datetime")
    assert _is_expired(link) is True


@pytest.mark.asyncio
async def test_mint_share_link_rejects_owner_role():
    from app.api.errors import BadRequestError
    from app.models.edges import COLLABORATES_ON, CONTAINS, IS_MEMBER_OF
    from app.models.nodes import Track, User, Workspace
    from app.utils.time import utc_now_iso

    owner = await User.create(display_name="Owner")
    now = utc_now_iso()
    ws = await Workspace.create(
        kind="personal",
        workspace_type="personal",
        name="WS",
        name_fold="ws",
        created_at=now,
        updated_at=now,
    )
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)
    track = await Track.create(title="T", owner_id=owner.id, workspace_id=ws.id)
    await owner.connect(track, edge=COLLABORATES_ON, role="owner", added_at=now)

    with pytest.raises(BadRequestError, match="Role must be one of"):
        await mint_share_link(owner.id, "track", track.id, role="owner")


@pytest.mark.asyncio
async def test_public_intent_mint_and_redeem_paths():
    from app.api.errors import BadRequestError
    from app.models.edges import COLLABORATES_ON, CONTAINS, IS_MEMBER_OF, OWNS
    from app.models.nodes import Entry, EntryType, ShareLink, Track, User, Workspace
    from app.utils.time import utc_now_iso

    owner = await User.create(display_name="Owner")
    other = await User.create(display_name="Other")
    now = utc_now_iso()
    ws = await Workspace.create(
        kind="personal",
        workspace_type="personal",
        name="WS",
        name_fold="ws",
        created_at=now,
        updated_at=now,
    )
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)
    track = await Track.create(title="T", owner_id=owner.id, workspace_id=ws.id)
    et = await EntryType.create(name="Note", name_fold="note", track_id=track.id)
    entry = await Entry.create(
        title="E",
        track_id=track.id,
        type_id=et.id,
        author_id=owner.id,
    )
    await track.connect(entry, edge=CONTAINS, added_at=now)
    await owner.connect(track, edge=OWNS)
    await owner.connect(entry, edge=COLLABORATES_ON, role="owner", added_at=now)

    pub = await mint_share_link(
        owner.id, "entry", entry.id, role="viewer", intent="public"
    )
    link = await ShareLink.get(pub["share_link"]["id"])
    assert link is not None
    assert link_intent(link) == "public"

    with pytest.raises(BadRequestError, match="public viewing"):
        await redeem_share_link(other.id, pub["token"])


@pytest.mark.asyncio
async def test_get_attached_operational_model_forbidden_without_view():
    from app.models.edges import COLLABORATES_ON, CONTAINS, IS_MEMBER_OF, OWNS
    from app.models.nodes import Entry, EntryType, Track, User, Workspace
    from app.services.operational_model_authoring import get_attached_operational_model
    from app.utils.time import utc_now_iso

    owner = await User.create(display_name="Owner")
    stranger = await User.create(display_name="Stranger")
    now = utc_now_iso()
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="WS",
        name_fold="ws",
        created_at=now,
        updated_at=now,
    )
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)
    track = await Track.create(title="Private", owner_id=owner.id, workspace_id=ws.id)
    et = await EntryType.create(name="Note", name_fold="note", track_id=track.id)
    entry = await Entry.create(
        title="E",
        track_id=track.id,
        type_id=et.id,
        author_id=owner.id,
    )
    await track.connect(entry, edge=CONTAINS, added_at=now)
    await owner.connect(track, edge=OWNS)

    result = await get_attached_operational_model(
        user_id=stranger.id,
        track_id=track.id,
    )
    assert result.get("error") == "forbidden"
