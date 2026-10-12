"""Persistent graph relationship clocks survive removal without granting rights."""

import pytest
from jvspatial.core.context import get_default_context

import app.models.edges as edges
from app.exceptions import AuthorizationUnavailableError
from app.models.nodes import User
from app.services import grant_history as history
from app.services.app_graph import catalog_user
from app.services.invitation_grants import _current_grant
from app.services.membership_write_scope import membership_write_scope
from tests.test_resource_location import graph

pytestmark = pytest.mark.smoke


async def fixture():
    """Create rooted synthetic graph principals and targets."""
    _, ws, app, _, _ = await graph()
    user = await User.create(display_name="Synthetic relationship clock")
    await catalog_user(user)
    return user, ws, app


async def baseline(user, target, kind):
    """Read the exact current primary grant and relationship clock."""
    row = await _current_grant(user.id, target.id, kind)
    return await history.grant_baseline(
        source_id=user.id, target_id=target.id, grant_class=kind, grant=row
    )


async def create(user, target, kind, role):
    """Create a grant and persist its accompanying history clock."""
    before = await _current_grant(user.id, target.id, kind)
    edge = await user.connect(target, edge=kind, role=role)
    after = await _current_grant(user.id, target.id, kind)
    await history.record_grant_change(
        source=user, target=target, grant_class=kind, before=before, after=after
    )
    return edge


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [edges.IS_MEMBER_OF, edges.COLLABORATES_ON])
async def test_absent_create_remove_recreate_clock(kind):
    """Preserve distinct lifetimes across grant absence and recreation."""
    user, ws, app = await fixture()
    target = ws if kind is edges.IS_MEMBER_OF else app
    role = "member" if kind is edges.IS_MEMBER_OF else "viewer"
    async with membership_write_scope(ws.id):
        user = await User.find_one({"id": user.id})
        app = await type(app).find_one({"id": app.id})
        target = await type(target).find_one({"id": target.id})
        original = await baseline(user, target, kind)
        edge = await create(user, target, kind, role)
        active = await baseline(user, target, kind)
        before = await _current_grant(user.id, target.id, kind)
        await edge.delete()
        clock = await history.record_grant_change(
            source=user, target=target, grant_class=kind, before=before, after=None
        )
        absent = await baseline(user, target, kind)
        recreated = await create(user, target, kind, role)
        final = await baseline(user, target, kind)
    assert len({original, active, absent, final}) == 4
    assert clock["context"]["revision"] == 2 and clock["context"]["grant_id"] is None
    assert clock["context"]["last_grant_id"] == edge.id
    assert recreated.id != edge.id
    assert (await history.read_grant_history(user.id, target.id, kind))["context"][
        "revision"
    ] == 3


@pytest.mark.asyncio
async def test_role_roundtrip_clock_and_exact_noop():
    """Distinguish role roundtrips while preserving exact no-op history."""
    user, ws, app = await fixture()
    async with membership_write_scope(ws.id):
        user = await User.find_one({"id": user.id})
        app = await type(app).find_one({"id": app.id})
        await user.connect(app, edge=edges.COLLABORATES_ON, role="viewer")
        original = await baseline(user, app, edges.COLLABORATES_ON)
        for role in ("editor", "viewer"):
            before = await _current_grant(user.id, app.id, edges.COLLABORATES_ON)
            await get_default_context().database.find_one_and_update(
                "edge", {"id": before["id"]}, {"$set": {"context.role": role}}
            )
            after = await _current_grant(user.id, app.id, edges.COLLABORATES_ON)
            await history.record_grant_change(
                source=user,
                target=app,
                grant_class=edges.COLLABORATES_ON,
                before=before,
                after=after,
            )
        final = await baseline(user, app, edges.COLLABORATES_ON)
        row = await _current_grant(user.id, app.id, edges.COLLABORATES_ON)
        unchanged = await history.record_grant_change(
            source=user,
            target=app,
            grant_class=edges.COLLABORATES_ON,
            before=row,
            after=row,
        )
    assert original != final
    assert unchanged["context"]["revision"] == 2
    assert await baseline(user, app, edges.COLLABORATES_ON) == final


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("revision", True),
        ("revision", 0),
        ("revision", -1),
        ("revision", "1"),
        ("grant_id", 12),
        ("last_grant_id", None),
        ("grant_fingerprint", "not-a-digest"),
        ("changed_at", None),
        ("changed_at", "2026-10-11"),
        ("changed_at", "not-a-date"),
    ],
)
async def test_malformed_clock_denies(field, value):
    """Refuse malformed history instead of accepting permission defaults."""
    user, ws, app = await fixture()
    async with membership_write_scope(ws.id):
        user = await User.find_one({"id": user.id})
        app = await type(app).find_one({"id": app.id})
        await create(user, app, edges.COLLABORATES_ON, "viewer")
        clock = await history.read_grant_history(user.id, app.id, edges.COLLABORATES_ON)
        await get_default_context().database.find_one_and_update(
            "edge", {"id": clock["id"]}, {"$set": {f"context.{field}": value}}
        )
        with pytest.raises(AuthorizationUnavailableError):
            await baseline(user, app, edges.COLLABORATES_ON)


@pytest.mark.asyncio
async def test_out_of_band_write_requires_explicit_recovery():
    """Reject a current grant that disagrees with its tracked history."""
    user, ws, app = await fixture()
    async with membership_write_scope(ws.id):
        user = await User.find_one({"id": user.id})
        app = await type(app).find_one({"id": app.id})
        edge = await create(user, app, edges.COLLABORATES_ON, "viewer")
        await get_default_context().database.find_one_and_update(
            "edge", {"id": edge.id}, {"$set": {"context.role": "editor"}}
        )
        with pytest.raises(AuthorizationUnavailableError):
            await baseline(user, app, edges.COLLABORATES_ON)


@pytest.mark.asyncio
async def test_absent_baseline_binds_recipient_and_target():
    """Bind absence to the exact recipient, target and grant family."""
    user, ws, app = await fixture()
    other = await User.create(display_name="Different synthetic recipient")
    await catalog_user(other)
    assert await baseline(user, app, edges.COLLABORATES_ON) != await baseline(
        other, app, edges.COLLABORATES_ON
    )
    assert await baseline(user, app, edges.COLLABORATES_ON) != await baseline(
        user, ws, edges.IS_MEMBER_OF
    )


@pytest.mark.asyncio
async def test_history_and_grant_rollback_together_on_postgres():
    """Roll back the history clock and grant in the same held transaction."""
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
    )

    if not graph_transaction_available(get_default_context().database):
        pytest.skip("transactional shared-store proof")
    user, ws, app = await fixture()
    with pytest.raises(RuntimeError, match="roll back clock"):
        async with membership_write_scope(ws.id):
            user = await User.find_one({"id": user.id})
            app = await type(app).find_one({"id": app.id})
            await create(user, app, edges.COLLABORATES_ON, "viewer")
            assert await history.read_grant_history(
                user.id, app.id, edges.COLLABORATES_ON
            )
            raise RuntimeError("roll back clock")
    assert await _current_grant(user.id, app.id, edges.COLLABORATES_ON) is None
    assert (
        await history.read_grant_history(user.id, app.id, edges.COLLABORATES_ON) is None
    )


@pytest.mark.asyncio
async def test_matching_technical_legacy_revision_has_one_fingerprint():
    """Normalize only a matching strictly typed legacy revision mirror."""
    user, ws, app = await fixture()
    await user.connect(app, edge=edges.COLLABORATES_ON, role="viewer")
    row = await _current_grant(user.id, app.id, edges.COLLABORATES_ON)
    original = history.grant_fingerprint(row)
    assert history.grant_fingerprint({**row, "grant_revision": 0}) == original
    for invalid in (True, "0", -1, 1, 0.0):
        with pytest.raises(AuthorizationUnavailableError):
            history.grant_fingerprint({**row, "grant_revision": invalid})


@pytest.mark.asyncio
@pytest.mark.parametrize("known_recipient", [True, False])
async def test_member_add_remove_invalidates_pending_absence_intent(known_recipient):
    """Prevent removed memberships from restoring an old absent baseline."""
    from app.services.invitation_grants import (
        apply_invitation_grant,
        capture_grant_baseline,
    )
    from app.services.workspace_membership import add_member, remove_member

    owner, workspace, _, _, _ = await graph()
    member = await User.create(display_name="Member history producer")
    await catalog_user(member)
    original = await capture_grant_baseline(
        member.id if known_recipient else None, workspace.id, workspace_membership=True
    )
    await add_member(
        actor_id=owner.id, workspace_id=workspace.id, member_id=member.id, role="member"
    )
    await remove_member(
        actor_id=owner.id, workspace_id=workspace.id, member_id=member.id
    )
    assert (
        await capture_grant_baseline(member.id, workspace.id, workspace_membership=True)
        != original
    )
    async with membership_write_scope(workspace.id):
        member = await User.find_one({"id": member.id})
        workspace = await type(workspace).find_one({"id": workspace.id})
        outcome = await apply_invitation_grant(
            member,
            workspace,
            baseline=original,
            workspace_membership=True,
            role="admin",
        )
        assert outcome["outcome"] == "conflict" and outcome["role"] is None
        assert await _current_grant(member.id, workspace.id, edges.IS_MEMBER_OF) is None
        clock = await history.read_grant_history(
            member.id, workspace.id, edges.IS_MEMBER_OF
        )
        assert (
            clock["context"]["revision"] == 2 and clock["context"]["grant_id"] is None
        )


@pytest.mark.asyncio
async def test_collaborator_producers_order_role_roundtrip_and_departure_history():
    """Exercise actual collaborator writers and departure clock advancement."""
    from app.services.invitation_grants import (
        apply_invitation_grant,
        capture_grant_baseline,
    )
    from app.services.sharing import add_collaborator, update_collaborator_role
    from app.services.workspace_membership import remove_member

    owner, workspace, app, _, _ = await graph()
    member = await User.create(display_name="Collaboration history producer")
    await catalog_user(member)
    first = await add_collaborator(owner.id, "app", app.id, member.id, "viewer")
    assert first["role"] == "viewer"
    original = await capture_grant_baseline(
        member.id, app.id, workspace_membership=False
    )
    await update_collaborator_role(owner.id, "app", app.id, member.id, "editor")
    await update_collaborator_role(owner.id, "app", app.id, member.id, "viewer")
    assert (
        await capture_grant_baseline(member.id, app.id, workspace_membership=False)
        != original
    )
    row = await _current_grant(member.id, app.id, edges.COLLABORATES_ON)
    assert row["context"]["grant_revision"] == 2
    unchanged = await add_collaborator(owner.id, "app", app.id, member.id, "admin")
    assert unchanged["outcome"] == "unchanged" and unchanged["role"] == "viewer"
    async with membership_write_scope(workspace.id):
        current = await User.find_one({"id": member.id})
        target = await type(app).find_one({"id": app.id})
        outcome = await apply_invitation_grant(
            current, target, baseline=original, workspace_membership=False, role="admin"
        )
        assert outcome["outcome"] == "conflict" and outcome["role"] == "viewer"
    await remove_member(
        actor_id=owner.id, workspace_id=workspace.id, member_id=member.id
    )
    assert await _current_grant(member.id, app.id, edges.COLLABORATES_ON) is None
    clock = await history.read_grant_history(member.id, app.id, edges.COLLABORATES_ON)
    assert clock["context"]["grant_id"] is None and clock["context"]["revision"] == 4
    assert (
        await capture_grant_baseline(member.id, app.id, workspace_membership=False)
        != original
    )


@pytest.mark.asyncio
async def test_member_scope_joins_same_task_and_rejects_child_or_foreign_scope():
    """Join only the same task, workspace and held database handle."""
    import asyncio

    _, workspace, _, _, _ = await graph()
    async with membership_write_scope(workspace.id):
        async with membership_write_scope(workspace.id):
            pass
        with pytest.raises(AuthorizationUnavailableError):
            async with membership_write_scope("foreign-workspace"):
                pass

        async def child():
            async with membership_write_scope(workspace.id):
                raise AssertionError("child must never inherit held scope")

        with pytest.raises(AuthorizationUnavailableError):
            await asyncio.create_task(child())
