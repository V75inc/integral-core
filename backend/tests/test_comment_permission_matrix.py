"""Who may comment on an entry — the matrix behind a recurring QA report.

"Users with Full Permissions Cannot Add Comments" has now been filed twice
(July 1, marked **Failed QA**; again on August 5). Neither filing says which
role the tester held, and the July screenshot shows the reporter as the
workspace **owner** — a case that demonstrably works (reproduced against the
dev database on the exact track in that screenshot: the comment posted).

So the interesting question is not "is commenting broken" but "which role
believes it has full permissions and is refused". That is a matrix, and the
answer was undocumented anywhere executable. This file makes it explicit.

The gate under test is the one the endpoints actually call:
``policy_engine.evaluate(action="comment.create", …)`` with an entry-kind
resource, which resolves `commenter`-or-better on that ENTRY (policy_engine.py,
the `_COMMENT_TIER_PREFIXES` branch). The scope still names the parent track —
that is how the cascade is expressed — but the role is resolved where the deny
edge lives.

The case that report turned out to be about is the org workspace **admin** with
no per-resource grant. ``workspace_staff_implicit_resource_role`` capped staff
at `viewer` ("inventory visibility only"), one rank below the comment gate, so
an admin could read an entry and not reply to it. That cap is now `commenter`
(ARCHITECTURE §9.5); ``test_org_admin_without_a_direct_grant_can_comment``
pins the new contract and ``test_org_admin_still_cannot_edit_or_administer``
pins the half that did not move.

The second group covers the deny edge. ``comment.*`` is scoped
``track:<id>`` — the parent, because that is how the cascade is expressed —
and the engine used to resolve the role there, which made per-ENTRY
``EXCLUDED_FROM`` invisible: an excluded user could POST a comment on an entry
whose GET already refused them. Entry-targeted comment and reaction actions now
resolve on the entry.
"""

import pytest

from app.models.edges import (
    COLLABORATES_ON,
    CONTAINS,
    EXCLUDED_FROM,
    IS_MEMBER_OF,
    OWNS,
)
from app.models.nodes import App, Entry, Track, User, Workspace
from app.schemas.policy import Resource, Subject
from app.services.permissions import resolve_role
from app.services.policy_engine import evaluate as policy_evaluate

pytestmark = pytest.mark.smoke


async def _may(user: User, entry: Entry, action: str) -> bool:
    """Evaluate as the endpoints do: entry-kind resource, track-scoped.

    The scope names the parent track (that is how the cascade is expressed);
    the role resolves on the entry, which is where a deny edge can live.
    """
    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user.id),
        action=action,
        resource=Resource(
            kind="entry",
            id=entry.id,
            scope=f"track:{entry.track_id or ''}",
        ),
    )
    return decision.allowed


async def _may_comment(user: User, entry: Entry) -> bool:
    return await _may(user, entry, "comment.create")


async def _org_fixture(label: str):
    """Org workspace → app → track → entry, fully edge-wired (I-GRAPH-01)."""
    ws = await Workspace.create(
        kind="organization", name=f"Org {label}", name_fold=f"org {label}"
    )
    app = await App.create(
        name=f"App {label}", name_fold=f"app {label}", workspace_id=ws.id
    )
    track = await Track.create(
        title=f"Track {label}", title_fold=f"track {label}", workspace_id=ws.id
    )
    entry = await Entry.create(title=f"Entry {label}", track_id=track.id)
    await ws.connect(app, edge=CONTAINS)
    await app.connect(track, edge=CONTAINS)
    await track.connect(entry, edge=CONTAINS)
    return ws, app, track, entry


async def _member(ws: Workspace, label: str, role: str) -> User:
    user = await User.create(user_id=f"cm_{label}", display_name=f"User {label}")
    await user.connect(ws, edge=IS_MEMBER_OF, role=role)
    return user


@pytest.mark.asyncio
async def test_track_commenter_can_comment():
    """The floor of the role ladder. If this fails, commenting is broken."""
    ws, _app, track, entry = await _org_fixture("commenter")
    user = await _member(ws, "commenter", "member")
    await user.connect(track, edge=COLLABORATES_ON, role="commenter")

    assert await resolve_role(user.id, "entry", entry.id) == "commenter"
    assert await _may_comment(user, entry) is True


@pytest.mark.asyncio
async def test_app_editor_can_comment_through_the_cascade():
    """App → Track → Entry cascade must survive to the comment gate.

    This is the regression the previous fix targeted: a visibility grant
    (viewer) must not eclipse a stronger App collaborator cascade, or an App
    editor resolves as viewer and is refused.
    """
    ws, app, _track, entry = await _org_fixture("appeditor")
    user = await _member(ws, "appeditor", "member")
    await user.connect(app, edge=COLLABORATES_ON, role="editor")

    assert await _may_comment(user, entry) is True


@pytest.mark.asyncio
async def test_track_viewer_cannot_comment():
    ws, _app, track, entry = await _org_fixture("viewer")
    user = await _member(ws, "viewer", "member")
    await user.connect(track, edge=COLLABORATES_ON, role="viewer")

    assert await _may_comment(user, entry) is False


@pytest.mark.asyncio
async def test_excluded_user_cannot_comment_despite_app_grant():
    """EXCLUDED_FROM overrides the inherited path, not a direct grant."""
    ws, app, track, entry = await _org_fixture("excluded")
    user = await _member(ws, "excluded", "member")
    await user.connect(app, edge=COLLABORATES_ON, role="editor")
    await user.connect(track, edge=EXCLUDED_FROM)

    assert await _may_comment(user, entry) is False


@pytest.mark.asyncio
async def test_plain_org_member_without_a_grant_cannot_comment():
    """Workspace membership does not cascade to children (ARCHITECTURE §9.5 rule 2)."""
    ws, _app, _track, entry = await _org_fixture("plainmember")
    user = await _member(ws, "plainmember", "member")

    assert await resolve_role(user.id, "entry", entry.id) is None
    assert await _may_comment(user, entry) is False


@pytest.mark.asyncio
async def test_org_admin_without_a_direct_grant_can_comment():
    """**The subject of the QA report, and what changed.**

    An org workspace admin holds every workspace-level power — member pool,
    invitations, settings — which is what "full permissions" means to a person
    filing a bug. On resources they get the implicit staff role, and that role
    was capped at `viewer` ("inventory visibility only"), one rank BELOW
    `commenter`. They could read an entry, had no way to reply to it, and got
    no explanation why.

    Now `commenter`: read and participate. The cap still sits below `editor`,
    so the authority half is untouched — see
    ``test_org_admin_still_cannot_edit_or_administer`` below, and
    ``test_org_admin_cannot_mint_share_without_direct_grant`` in
    tests/test_wave1_access.py.
    """
    ws, _app, _track, entry = await _org_fixture("orgadmin")
    admin = await _member(ws, "orgadmin", "admin")

    assert await resolve_role(admin.id, "entry", entry.id) == "commenter"
    assert await _may_comment(admin, entry) is True


@pytest.mark.asyncio
async def test_org_admin_still_cannot_edit_or_administer():
    """The other half of the same change — the part that must NOT move.

    Raising the implicit role to `commenter` buys participation and nothing
    else. If a later change pushes it to `editor`, these are what fail.
    """
    from app.services.permissions import can_admin_track, can_edit_track

    ws, _app, track, _entry = await _org_fixture("orgadminlimits")
    admin = await _member(ws, "orgadminlimits", "admin")

    assert await can_edit_track(admin.id, track.id) is False
    assert await can_admin_track(admin.id, track.id) is False


@pytest.mark.asyncio
async def test_entry_level_exclusion_blocks_commenting():
    """The deny edge that the track-scoped gate could not see.

    ``comment.create`` is scoped ``track:<id>``, and the engine resolved the
    role there — so an ``EXCLUDED_FROM`` edge on a single ENTRY was invisible
    and a user excluded from that entry could still post on it. GET on the
    same entry already refused them (``entry.read`` → ``can_view_entry``,
    which honours the edge), so the two halves of one row disagreed: read
    denied, write allowed.
    """
    ws, _app, track, entry = await _org_fixture("entryexcluded")
    user = await _member(ws, "entryexcluded", "member")
    await user.connect(track, edge=COLLABORATES_ON, role="editor")
    await user.connect(entry, edge=EXCLUDED_FROM)

    # Still an editor one level up — this is exactly what made the hole
    # invisible to a track-level resolve.
    assert await resolve_role(user.id, "track", track.id) == "editor"
    assert await resolve_role(user.id, "entry", entry.id) is None
    assert await _may_comment(user, entry) is False
    assert await _may(user, entry, "comment.read") is False
    assert await _may(user, entry, "reaction.create") is False


@pytest.mark.asyncio
async def test_entry_level_exclusion_blocks_org_staff_too():
    """The implicit staff role is a candidate, not a bypass.

    Raising the staff cap to `commenter` must not give an org admin a way
    around a per-entry deny — the exclusion is checked before the candidate
    pool is assembled (ARCHITECTURE §9.5 phase 1).
    """
    ws, _app, _track, entry = await _org_fixture("staffexcluded")
    admin = await _member(ws, "staffexcluded", "admin")
    await admin.connect(entry, edge=EXCLUDED_FROM)

    assert await resolve_role(admin.id, "entry", entry.id) is None
    assert await _may_comment(admin, entry) is False


@pytest.mark.asyncio
async def test_reactions_ride_the_same_tier_as_comments():
    """``reaction.*`` shares the comment tier — pin it, don't assume it."""
    ws, _app, track, entry = await _org_fixture("reactions")
    commenter = await _member(ws, "reactions_c", "member")
    await commenter.connect(track, edge=COLLABORATES_ON, role="commenter")
    viewer = await _member(ws, "reactions_v", "member")
    await viewer.connect(track, edge=COLLABORATES_ON, role="viewer")

    assert await _may(commenter, entry, "reaction.create") is True
    assert await _may(viewer, entry, "reaction.create") is False


@pytest.mark.asyncio
async def test_reaction_gate_asymmetry_is_self_scoped():
    """`remove_reaction` gates entry.read while `add_reaction` gates commenter.

    Looks like a hole; is not one, because removal only ever deletes the
    CALLER's own id from the emoji list. The one user the read gate admits
    that the commenter gate would not is someone demoted commenter→viewer
    retracting a reaction they legitimately placed — which must keep working.
    Pinned so a future "tidy the gates for consistency" pass does not silently
    strand demoted users' reactions, and so the add side stays at commenter.
    """
    # This test drives the real handlers through invoke_route_in_process,
    # which resolves the principal — so unlike the pure policy tests above it
    # needs an AuthUser-backed principal, not a bare graph node. (A bare node
    # id comes back as a user_not_found envelope, not an exception: the first
    # version of this test "passed" its permission step against an empty
    # reactions dict for exactly that reason.)
    from jvspatial.api.auth.models import UserCreate

    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.auth import _get_auth_service
    from app.api.entries import add_reaction, remove_reaction
    from app.api.errors import InsufficientPermissionsError
    from app.models.nodes import Entry
    from app.services.app_graph import catalog_user

    ws, _app, track, entry = await _org_fixture("reactasym")
    resp = await _get_auth_service().register_user(
        UserCreate(email="reactasym@example.com", password="testpassword123")
    )
    principal = resp.id
    user = await User.create(user_id=principal, display_name="React Asym")
    await catalog_user(user)
    await user.connect(ws, edge=IS_MEMBER_OF, role="member")
    await user.connect(track, edge=COLLABORATES_ON, role="commenter")

    # As a commenter: react.
    await invoke_route_in_process(
        add_reaction,
        principal_id=principal,
        entry_id=entry.id,
        emoji="👍",
    )
    refreshed = await Entry.get(entry.id)
    # The handler records the principal id it resolved, which for an
    # invoke_route_in_process caller may be the AuthUser form rather than the
    # graph-node id — assert on the list's shape, then reuse whatever id it
    # stored to prove the removal below is the same actor self-removing.
    # COPY the list: the in-process cache hands back the same Entry object,
    # so this is otherwise the very list remove_reaction mutates — the final
    # assertion would then be reading its own success as an IndexError.
    stored = list((refreshed.reactions or {}).get("👍", []))
    assert len(stored) == 1, refreshed.reactions

    # Demote to viewer — adding is now refused…
    from app.models.edges import COLLABORATES_ON as _CO

    ctx = await user.get_context()
    for edge in await ctx.find_edges_between(user.id, track.id, edge_class=_CO):
        await edge.delete()
    await user.connect(track, edge=COLLABORATES_ON, role="viewer")
    from app.services import permissions_process_cache

    permissions_process_cache.invalidate_user(user.id)
    permissions_process_cache.invalidate_user(principal)

    with pytest.raises(InsufficientPermissionsError):
        await invoke_route_in_process(
            add_reaction,
            principal_id=principal,
            entry_id=entry.id,
            emoji="🎉",
        )

    # …but retracting the reaction they placed as a commenter still works.
    await invoke_route_in_process(
        remove_reaction,
        principal_id=principal,
        entry_id=entry.id,
        emoji="👍",
    )
    refreshed = await Entry.get(entry.id)
    assert stored[0] not in (refreshed.reactions or {}).get("👍", [])


@pytest.mark.asyncio
async def test_app_scoped_comment_actions_resolve_the_same_way():
    """The `app:` scope branch is a separate code path with the same rule."""
    ws, app, _track, entry = await _org_fixture("appscope")
    commenter = await _member(ws, "appscope_c", "member")
    await commenter.connect(app, edge=COLLABORATES_ON, role="commenter")
    outsider = await _member(ws, "appscope_o", "member")

    async def _app_scoped(user: User) -> bool:
        decision = await policy_evaluate(
            subject=Subject(kind="human", id=user.id),
            action="comment.create",
            resource=Resource(kind="entry", id=entry.id, scope=f"app:{app.id}"),
        )
        return decision.allowed

    assert await _app_scoped(commenter) is True
    assert await _app_scoped(outsider) is False


@pytest.mark.asyncio
async def test_the_approval_path_honours_entry_exclusion_too():
    """``create_comment_internal`` is a second door onto the same write.

    ``approval_executor`` calls it when a staged ``comment.create`` is
    approved, so it carries its own ``policy_evaluate`` — and it described the
    resource as ``kind="comment", id=""``. With nothing entry-shaped to
    resolve, the engine fell back to the track in the scope, and the exclusion
    was invisible again on that path alone: REST refused, approval posted.

    Asserted through the real helper rather than the engine, because the bug
    was in how the helper described the resource, not in the gate.
    """
    from app.services.comment_writer import create_comment_internal

    ws, _app, track, entry = await _org_fixture("approvalexcluded")
    user = await _member(ws, "approvalexcluded", "member")
    await user.connect(track, edge=COLLABORATES_ON, role="editor")
    await user.connect(entry, edge=EXCLUDED_FROM)

    with pytest.raises(PermissionError):
        await create_comment_internal(
            actor_kind="human",
            actor_id=user.id,
            payload={"entry_id": entry.id, "body": "posted around the deny edge"},
        )


@pytest.mark.asyncio
async def test_the_approval_path_still_lets_a_permitted_author_through():
    """The other direction — the fix must not close the door on everyone."""
    from app.services.comment_writer import create_comment_internal

    ws, _app, track, entry = await _org_fixture("approvalallowed")
    user = await _member(ws, "approvalallowed", "member")
    await user.connect(track, edge=COLLABORATES_ON, role="commenter")

    created = await create_comment_internal(
        actor_kind="human",
        actor_id=user.id,
        payload={"entry_id": entry.id, "body": "posted through the approval path"},
    )
    assert created.get("id")


@pytest.mark.asyncio
async def test_workspace_owner_with_a_direct_grant_can_comment():
    """The role in the July 1 screenshot. Reproduced working on dev."""
    ws, app, _track, entry = await _org_fixture("owner")
    owner = await _member(ws, "owner", "owner")
    await owner.connect(app, edge=OWNS)

    assert await _may_comment(owner, entry) is True


@pytest.mark.asyncio
async def test_moderation_sits_above_the_commenter_tier():
    """``comment.moderate`` is admin-tier, not commenter-tier.

    Deleting somebody else's comment is authorized by this action (see
    ``api/comments.delete_comment``). It is the one ``comment.*`` verb that
    must NOT ride ``_COMMENT_TIER_PREFIXES``: on that tier every commenter
    could remove every other person's words. Behaviour is covered end-to-end
    in test_public_comment_moderation.py; this pins the tier itself, next to
    the rest of the role matrix.
    """
    ws, app, track, entry = await _org_fixture("moderate")

    commenter = await _member(ws, "moderatecommenter", "member")
    await commenter.connect(track, edge=COLLABORATES_ON, role="commenter")
    editor = await _member(ws, "moderateeditor", "member")
    await editor.connect(track, edge=COLLABORATES_ON, role="editor")
    track_owner = await _member(ws, "moderateowner", "admin")
    await track_owner.connect(track, edge=OWNS)

    # Both may participate — so the refusals below are about the tier, not
    # about access to the track.
    assert await _may_comment(commenter, entry) is True
    assert await _may_comment(editor, entry) is True

    assert await _may(commenter, entry, "comment.moderate") is False
    assert await _may(editor, entry, "comment.moderate") is False
    assert await _may(track_owner, entry, "comment.moderate") is True


@pytest.mark.asyncio
async def test_whoever_can_open_public_commenting_can_moderate_it():
    """The property that makes the moderation gate the right one.

    ``update_public_track_share_settings`` admits ``track.share_link.mint``,
    which lands on ``can_admin_track``; ``comment.moderate`` lands there too.
    So the person who can turn public commenting ON is exactly the person who
    can clean up after it — there is no configuration in which a track has
    public comments nobody present is able to remove, which is the state the
    original author-only rule created.

    Pinned as a pair: if either action is ever re-tiered independently, this
    fails even though both endpoints still "work".
    """
    ws, app, track, entry = await _org_fixture("gatepair")

    # An App owner: inherits `editor` on the child track (roles above editor
    # are capped on cascade), so they administer neither — same answer to both
    # questions, which is the point.
    app_owner = await _member(ws, "gatepairapp", "member")
    await app_owner.connect(app, edge=OWNS)
    # A track owner: administers both.
    track_owner = await _member(ws, "gatepairtrack", "member")
    await track_owner.connect(track, edge=OWNS)

    async def _may_open_public_commenting(user: User) -> bool:
        """As ``update_public_track_share_settings`` asks it: track-kind.

        Not via ``_may`` — ``track.share_link.mint`` is dispatched by
        ``_evaluate_sharing_human_action``, which resolves the role on
        ``resource.id`` directly, so an entry-kind resource would silently
        resolve the ENTRY id as a track and answer False for everyone.
        """
        decision = await policy_evaluate(
            subject=Subject(kind="human", id=user.id),
            action="track.share_link.mint",
            resource=Resource(kind="track", id=track.id, scope=f"track:{track.id}"),
        )
        return decision.allowed

    for user, expected in ((app_owner, False), (track_owner, True)):
        may_open = await _may_open_public_commenting(user)
        may_moderate = await _may(user, entry, "comment.moderate")
        assert may_open is expected
        assert may_moderate is expected
        assert may_open == may_moderate
