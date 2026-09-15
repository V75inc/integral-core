"""Phase 2-5 tests — sharing service, share links, resource invitations,
and the cross-workspace "Shared with me" listing.

Direct service-level coverage (no HTTP) — exercises the same functions the
API endpoints delegate to.
"""

import pytest

from app.models.edges import COLLABORATES_ON, CONTAINS, IS_MEMBER_OF, OWNS
from app.models.nodes import App, Entry, Track, User, Workspace
from app.services.permissions import resolve_role
from app.services.share_links import (
    list_active_links,
    mint_share_link,
    redeem_share_link,
    revoke_share_link,
)
from app.services.sharing import (
    add_collaborator,
    add_exclusion,
    ensure_guest_membership,
    list_access,
    remove_collaborator,
    remove_exclusion,
)

# Part of the per-PR smoke gate (see pyproject [tool.pytest.ini_options] markers).
# CI bills only this subset; the full suite runs locally via `make verify` and nightly.
pytestmark = pytest.mark.smoke


async def _user(suffix: str) -> User:
    return await User.create(
        user_id=f"share_user_{suffix}", display_name=f"User {suffix}"
    )


async def _user_with_auth_email(email: str, display_name: str) -> User:
    """Graph User linked to an AuthUser with the given email (for invitation accept)."""
    from datetime import datetime, timezone

    from jvspatial.api.auth.models import User as AuthUser

    from app.services.invitations import _normalize_email

    normalized = await _normalize_email(email)
    auth_user = await AuthUser.find_one({"context.email": normalized})
    if not auth_user:
        auth_user = await AuthUser.create(
            email=normalized,
            password_hash="test-hash",
            name=display_name,
        )
    matches = await User.find({"context.user_id": auth_user.id})
    if matches:
        return matches[0]
    return await User.create(
        user_id=auth_user.id,
        display_name=display_name,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


async def _workspace(name: str, kind: str = "organization") -> Workspace:
    return await Workspace.create(kind=kind, name=name, name_fold=name.casefold())


async def _space(name: str, workspace_id: str = "") -> App:
    return await App.create(
        name=name, name_fold=name.casefold(), workspace_id=workspace_id
    )


async def _track(title: str, workspace_id: str = "") -> Track:
    return await Track.create(
        title=title, title_fold=title.casefold(), workspace_id=workspace_id
    )


async def _entry(title: str, track_id: str) -> Entry:
    return await Entry.create(title=title, track_id=track_id, author_id="rr")


# ---------------------------------------------------------------------------
# add_collaborator / remove_collaborator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_collaborator_grants_role_and_resolves():
    owner = await _user("addcollab_owner")
    target = await _user("addcollab_target")
    track = await _track("T")
    await owner.connect(track, edge=OWNS)
    await add_collaborator(owner.id, "track", track.id, target.id, role="editor")
    assert await resolve_role(target.id, "track", track.id) == "editor"


@pytest.mark.asyncio
async def test_add_collaborator_accepts_commenter_role():
    owner = await _user("addcommenter_owner")
    target = await _user("addcommenter_target")
    track = await _track("T")
    await owner.connect(track, edge=OWNS)
    await add_collaborator(owner.id, "track", track.id, target.id, role="commenter")
    assert await resolve_role(target.id, "track", track.id) == "commenter"


@pytest.mark.asyncio
async def test_add_collaborator_rejected_for_non_owner():
    owner = await _user("rej_owner")
    editor = await _user("rej_editor")
    target = await _user("rej_target")
    track = await _track("T")
    await owner.connect(track, edge=OWNS)
    await editor.connect(track, edge=COLLABORATES_ON, role="editor")
    from app.api.errors import InsufficientPermissionsError

    with pytest.raises(InsufficientPermissionsError):
        await add_collaborator(editor.id, "track", track.id, target.id, role="viewer")


@pytest.mark.asyncio
async def test_remove_collaborator_drops_role():
    owner = await _user("rmcollab_owner")
    target = await _user("rmcollab_target")
    track = await _track("T")
    await owner.connect(track, edge=OWNS)
    await add_collaborator(owner.id, "track", track.id, target.id, role="editor")
    await remove_collaborator(owner.id, "track", track.id, target.id)
    assert await resolve_role(target.id, "track", track.id) is None


# ---------------------------------------------------------------------------
# Auto-guest workspace membership on cross-workspace share
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_workspace_share_auto_grants_guest_membership():
    owner = await _user("autoguest_owner")
    target = await _user("autoguest_target")
    ws = await _workspace("Org W")
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner")
    track = await _track("T", workspace_id=ws.id)
    await owner.connect(track, edge=OWNS)
    # Target has no IS_MEMBER_OF on ws.
    result = await add_collaborator(
        owner.id, "track", track.id, target.id, role="viewer"
    )
    assert result["auto_added_to_workspace_pool"] is True
    # Verify guest IS_MEMBER_OF edge exists.
    ctx = await target.get_context()
    edges = await ctx.find_edges_between(target.id, ws.id, edge_class=IS_MEMBER_OF)
    assert any(getattr(e, "role", "") == "guest" for e in edges)


@pytest.mark.asyncio
async def test_ensure_guest_membership_idempotent():
    user = await _user("guest_idem")
    ws = await _workspace("W")
    first = await ensure_guest_membership(user, ws.id, "system")
    second = await ensure_guest_membership(user, ws.id, "system")
    assert first is True
    assert second is False


# ---------------------------------------------------------------------------
# Exclusions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_exclusion_blocks_inherited_access():
    owner = await _user("excl_owner")
    target = await _user("excl_target")
    ws = await _workspace("Add Excl W")
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner")
    await target.connect(ws, edge=IS_MEMBER_OF, role="member")
    sp = await _space("S", workspace_id=ws.id)
    track = await _track("T", workspace_id=ws.id)
    await owner.connect(sp, edge=OWNS)
    await target.connect(sp, edge=COLLABORATES_ON, role="editor")
    await sp.connect(track, edge=CONTAINS)
    # Owner of App owns the cascade chain; ensure they have access to
    # the track (cascade owner → editor) so the exclusion API permits action.
    await owner.connect(track, edge=OWNS)
    await add_exclusion(owner.id, "track", track.id, target.id)
    assert await resolve_role(target.id, "track", track.id) is None


@pytest.mark.asyncio
async def test_remove_exclusion_restores_inherited_access():
    owner = await _user("rmexcl_owner")
    target = await _user("rmexcl_target")
    ws = await _workspace("Rm Excl W")
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner")
    await target.connect(ws, edge=IS_MEMBER_OF, role="member")
    sp = await _space("S", workspace_id=ws.id)
    track = await _track("T", workspace_id=ws.id)
    await owner.connect(sp, edge=OWNS)
    await owner.connect(track, edge=OWNS)
    await target.connect(sp, edge=COLLABORATES_ON, role="editor")
    await sp.connect(track, edge=CONTAINS)
    await add_exclusion(owner.id, "track", track.id, target.id)
    await remove_exclusion(owner.id, "track", track.id, target.id)
    assert await resolve_role(target.id, "track", track.id) == "editor"


# ---------------------------------------------------------------------------
# list_access snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_access_returns_direct_inherited_excluded_buckets():
    owner = await _user("la_owner")
    direct_collab = await _user("la_direct")
    inherited_collab = await _user("la_inh")
    excluded = await _user("la_excl")
    ws = await _workspace("List Access W")
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner")
    for u in (direct_collab, inherited_collab, excluded):
        await u.connect(ws, edge=IS_MEMBER_OF, role="member")
    sp = await _space("S", workspace_id=ws.id)
    track = await _track("T", workspace_id=ws.id)
    await owner.connect(sp, edge=OWNS)
    await owner.connect(track, edge=OWNS)
    await sp.connect(track, edge=CONTAINS)
    await direct_collab.connect(track, edge=COLLABORATES_ON, role="commenter")
    await inherited_collab.connect(sp, edge=COLLABORATES_ON, role="editor")
    await excluded.connect(sp, edge=COLLABORATES_ON, role="viewer")
    await add_exclusion(owner.id, "track", track.id, excluded.id)

    snapshot = await list_access(owner.id, "track", track.id)
    direct_uids = {r["user_id"] for r in snapshot["direct"]}
    inherited_uids = {r["user_id"] for r in snapshot["inherited"]}
    excluded_uids = {r["user_id"] for r in snapshot["excluded"]}
    assert direct_collab.id in direct_uids
    assert inherited_collab.id in inherited_uids
    assert excluded.id in excluded_uids


# ---------------------------------------------------------------------------
# ShareLink mint / redeem / revoke
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_owner_cannot_mint_share_link():
    from app.api.errors import InsufficientPermissionsError

    owner = await _user("mint_owner")
    other = await _user("mint_other")
    track = await _track("MintAuthz")
    await owner.connect(track, edge=OWNS)
    with pytest.raises(InsufficientPermissionsError):
        await mint_share_link(other.id, "track", track.id, role="viewer")


@pytest.mark.asyncio
async def test_mint_and_redeem_share_link():
    owner = await _user("link_owner")
    target = await _user("link_target")
    track = await _track("T")
    await owner.connect(track, edge=OWNS)
    minted = await mint_share_link(owner.id, "track", track.id, role="commenter")
    token = minted["token"]
    redeemed = await redeem_share_link(target.id, token)
    assert redeemed["created_edge"] is True
    assert await resolve_role(target.id, "track", track.id) == "commenter"


@pytest.mark.asyncio
async def test_share_link_redeem_idempotent_for_existing_collab():
    owner = await _user("link_idem_owner")
    target = await _user("link_idem_target")
    track = await _track("T")
    await owner.connect(track, edge=OWNS)
    await target.connect(track, edge=COLLABORATES_ON, role="editor")
    minted = await mint_share_link(owner.id, "track", track.id, role="viewer")
    redeemed = await redeem_share_link(target.id, minted["token"])
    assert redeemed["created_edge"] is False
    # Existing higher role preserved.
    assert await resolve_role(target.id, "track", track.id) == "editor"


@pytest.mark.asyncio
async def test_revoked_link_cannot_be_redeemed():
    owner = await _user("revoke_owner")
    target = await _user("revoke_target")
    track = await _track("T")
    await owner.connect(track, edge=OWNS)
    minted = await mint_share_link(owner.id, "track", track.id, role="viewer")
    await revoke_share_link(owner.id, minted["share_link"]["id"])
    from app.api.errors import BadRequestError

    with pytest.raises(BadRequestError):
        await redeem_share_link(target.id, minted["token"])


@pytest.mark.asyncio
async def test_list_active_links_excludes_revoked():
    owner = await _user("listlinks_owner")
    track = await _track("T")
    await owner.connect(track, edge=OWNS)
    a = await mint_share_link(owner.id, "track", track.id, role="viewer")
    b = await mint_share_link(owner.id, "track", track.id, role="editor")
    await revoke_share_link(owner.id, a["share_link"]["id"])
    active = await list_active_links(owner.id, "track", track.id)
    ids = {row["id"] for row in active}
    assert b["share_link"]["id"] in ids
    assert a["share_link"]["id"] not in ids


@pytest.mark.asyncio
async def test_list_active_links_requires_owner():
    owner = await _user("listlinks_authz_owner")
    stranger = await _user("listlinks_authz_stranger")
    track = await _track("T")
    await owner.connect(track, edge=OWNS)
    await mint_share_link(owner.id, "track", track.id, role="viewer")
    from app.api.errors import InsufficientPermissionsError

    with pytest.raises(InsufficientPermissionsError):
        await list_active_links(stranger.id, "track", track.id)


@pytest.mark.asyncio
async def test_share_link_cross_workspace_grants_guest_membership():
    owner = await _user("sl_xws_owner")
    target = await _user("sl_xws_target")
    ws = await _workspace("Org SL")
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner")
    track = await _track("T", workspace_id=ws.id)
    await owner.connect(track, edge=OWNS)
    minted = await mint_share_link(owner.id, "track", track.id, role="viewer")
    await redeem_share_link(target.id, minted["token"])
    ctx = await target.get_context()
    edges = await ctx.find_edges_between(target.id, ws.id, edge_class=IS_MEMBER_OF)
    assert any(getattr(e, "role", "") == "guest" for e in edges)


# ---------------------------------------------------------------------------
# Resource-level invitation accept materializes COLLABORATES_ON
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resource_invitation_accept_materializes_collab_edge():
    from app.services.invitations import (
        consume_invitation_token,
        create_resource_invitation,
    )

    owner = await _user("inv_owner")
    track = await _track("T")
    await owner.connect(track, edge=OWNS)
    invitation, plaintext, _ = await create_resource_invitation(
        resource_type="track",
        resource_id=track.id,
        workspace_id="",
        inviter_user_id=owner.id,
        email="invitee@example.com",
        role="commenter",
        send_email_notification=False,
    )
    accepting = await _user_with_auth_email("invitee@example.com", "Invitee")
    inv, err = await consume_invitation_token(plaintext, accepting.id)
    assert err is None
    assert inv is not None
    assert await resolve_role(accepting.id, "track", track.id) == "commenter"


@pytest.mark.asyncio
async def test_resource_invitation_stays_pending_when_resource_missing():
    from app.models.nodes import Invitation
    from app.services.invitations import (
        ERR_RESOURCE_NOT_FOUND,
        consume_invitation_token,
        create_resource_invitation,
    )

    owner = await _user("inv_miss_owner")
    track = await _track("T")
    await owner.connect(track, edge=OWNS)
    invitation, plaintext, _ = await create_resource_invitation(
        resource_type="track",
        resource_id=track.id,
        workspace_id="",
        inviter_user_id=owner.id,
        email="missing@example.com",
        role="viewer",
        send_email_notification=False,
    )
    accepting = await _user_with_auth_email("missing@example.com", "Missing")
    broken = await Invitation.get(invitation.id)
    broken.target_resource_id = "n.Track.nonexistent_resource_id"
    await broken.save()

    inv, err = await consume_invitation_token(plaintext, accepting.id)
    assert err == ERR_RESOURCE_NOT_FOUND
    assert inv is not None
    reloaded = await Invitation.get(invitation.id)
    assert reloaded.status == "pending"


@pytest.mark.asyncio
async def test_resource_invitation_wires_invited_to_edges():
    from app.models.edges import INVITED_TO
    from app.services.invitations import create_resource_invitation

    owner = await _user("inv_edge_owner")
    track = await _track("T")
    await owner.connect(track, edge=OWNS)
    invitation, _, _ = await create_resource_invitation(
        resource_type="track",
        resource_id=track.id,
        workspace_id="",
        inviter_user_id=owner.id,
        email="edges@example.com",
        role="viewer",
        send_email_notification=False,
    )
    ctx = await invitation.get_context()
    to_track = await ctx.find_edges_between(
        invitation.id, track.id, edge_class=INVITED_TO
    )
    assert to_track


@pytest.mark.asyncio
async def test_expire_invitations_only_marks_past_pending():
    from datetime import datetime, timedelta

    from app.models.nodes import Invitation
    from app.services.invitations import _expiry_iso, expire_invitations

    future = await Invitation.create(
        workspace_id="ws-future",
        email="future@example.com",
        status="pending",
        expires_at=_expiry_iso(30),
        token_hash="a" * 64,
        created_at=datetime.now().isoformat(),
    )
    past = await Invitation.create(
        workspace_id="ws-past",
        email="past@example.com",
        status="pending",
        expires_at=(datetime.now() - timedelta(days=1)).isoformat(),
        token_hash="b" * 64,
        created_at=datetime.now().isoformat(),
    )
    count = await expire_invitations()
    assert count >= 1
    reloaded_future = await Invitation.get(future.id)
    reloaded_past = await Invitation.get(past.id)
    assert reloaded_future.status == "pending"
    assert reloaded_past.status == "expired"


@pytest.mark.asyncio
async def test_consume_rejects_email_mismatch():
    from app.services.invitations import (
        ERR_EMAIL_MISMATCH,
        consume_invitation_token,
        create_resource_invitation,
    )

    owner = await _user("inv_mismatch_owner")
    track = await _track("T")
    await owner.connect(track, edge=OWNS)
    _, plaintext, _ = await create_resource_invitation(
        resource_type="track",
        resource_id=track.id,
        workspace_id="",
        inviter_user_id=owner.id,
        email="intended@example.com",
        role="viewer",
        send_email_notification=False,
    )
    wrong_user = await _user_with_auth_email("other@example.com", "Other")
    inv, err = await consume_invitation_token(plaintext, wrong_user.id)
    assert err == ERR_EMAIL_MISMATCH
    assert inv is not None


# ---------------------------------------------------------------------------
# Race-safe edge upserts (idempotency)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_collaborator_honors_workspace_gate_with_guest_membership():
    owner = await _user("ws_gate_owner")
    target = await _user("ws_gate_target")
    ws = await _workspace("Gate WS", kind="personal")
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner")
    track = await _track("Gate T", workspace_id=ws.id)
    await owner.connect(track, edge=OWNS)
    await add_collaborator(owner.id, "track", track.id, target.id, role="viewer")
    assert await resolve_role(target.id, "track", track.id) == "viewer"


@pytest.mark.asyncio
async def test_add_collaborator_is_idempotent():
    owner = await _user("idem_collab_owner")
    target = await _user("idem_collab_target")
    track = await _track("Idem T")
    await owner.connect(track, edge=OWNS)
    await add_collaborator(owner.id, "track", track.id, target.id, role="editor")
    await add_collaborator(owner.id, "track", track.id, target.id, role="editor")
    ctx = await target.get_context()
    edges = await ctx.find_edges_between(
        target.id, track.id, edge_class=COLLABORATES_ON
    )
    assert len(edges) == 1


@pytest.mark.asyncio
async def test_add_exclusion_is_idempotent():
    owner = await _user("idem_excl_owner")
    target = await _user("idem_excl_target")
    track = await _track("Idem T2")
    await owner.connect(track, edge=OWNS)
    await add_collaborator(owner.id, "track", track.id, target.id, role="editor")
    await add_exclusion(owner.id, "track", track.id, target.id, reason="dup test")
    await add_exclusion(owner.id, "track", track.id, target.id, reason="dup test")
    from app.models.edges import EXCLUDED_FROM

    ctx = await target.get_context()
    edges = await ctx.find_edges_between(target.id, track.id, edge_class=EXCLUDED_FROM)
    assert len(edges) == 1


@pytest.mark.asyncio
async def test_commenter_may_post_comments_but_not_edit_entries():
    """The `commenter` role must clear comment.create but not entry.update.

    Regression: the comment endpoints used to carry their own
    `ROLE_RANK >= commenter` gate and delegate the coarse check to
    `policy_engine.evaluate`. When that endpoint-side gate was removed,
    `comment.create` fell through to the entry tier (`can_edit_track`,
    which starts at *editor*), locking commenters out of the one action
    the role exists to grant.
    """
    from app.schemas.policy import Resource, Subject
    from app.services.policy_engine import evaluate as policy_evaluate

    owner = await _user("commenter_gate_owner")
    commenter = await _user("commenter_gate_user")
    track = await _track("Commenter Gate T")
    await owner.connect(track, edge=OWNS)
    await add_collaborator(owner.id, "track", track.id, commenter.id, role="commenter")

    scope = f"track:{track.id}"

    may_comment = await policy_evaluate(
        subject=Subject(kind="human", id=commenter.id),
        action="comment.create",
        resource=Resource(kind="track", id=track.id, scope=scope),
    )
    assert may_comment.allowed, "commenter must be able to post comments"

    may_edit = await policy_evaluate(
        subject=Subject(kind="human", id=commenter.id),
        action="entry.update",
        resource=Resource(kind="track", id=track.id, scope=scope),
    )
    assert not may_edit.allowed, "commenter must NOT gain entry-edit rights"


@pytest.mark.asyncio
async def test_viewer_may_not_post_comments():
    """A plain viewer stays below the comment tier."""
    from app.schemas.policy import Resource, Subject
    from app.services.policy_engine import evaluate as policy_evaluate

    owner = await _user("viewer_gate_owner")
    viewer = await _user("viewer_gate_user")
    track = await _track("Viewer Gate T")
    await owner.connect(track, edge=OWNS)
    await add_collaborator(owner.id, "track", track.id, viewer.id, role="viewer")

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=viewer.id),
        action="comment.create",
        resource=Resource(kind="track", id=track.id, scope=f"track:{track.id}"),
    )
    assert not decision.allowed, "viewer must not be able to post comments"
