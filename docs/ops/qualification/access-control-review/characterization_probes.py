"""Temporary characterization probes for the October 2026 access review."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.middleware.permissions_cache import isolated_permissions_cache
from app.models.edges import (
    COLLABORATES_ON,
    CONTAINS,
    EXCLUDED_FROM,
    IS_MEMBER_OF,
    OWNS,
)
from app.models.nodes import App, Entry, Track, User, Workspace
from app.schemas.policy import Resource, Subject
from app.services import permissions as perms
from app.services import permissions_process_cache as pc
from app.services import policy_engine as pe
from app.services.app_graph import (
    catalog_app,
    catalog_track,
    catalog_user,
    catalog_workspace,
)
from app.services.share_links import (
    mint_share_link,
    redeem_share_link,
    revoke_share_link,
)
from app.services.sharing import add_collaborator, add_exclusion, remove_collaborator


async def fixture_graph():
    owner = await User.create(user_id="audit-owner")
    await catalog_user(owner)
    member = await User.create(user_id="audit-member")
    await catalog_user(member)
    ws = await Workspace.create(name="Audit", kind="organization")
    await catalog_workspace(ws)
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner")
    await member.connect(ws, edge=IS_MEMBER_OF, role="member")
    app = await App.create(name="Audit App", workspace_id=ws.id, visibility="private")
    await catalog_app(app)
    await owner.connect(app, edge=OWNS)
    track = await Track.create(
        title="Audit Track", workspace_id=ws.id, visibility="inherit"
    )
    await catalog_track(track)
    await owner.connect(track, edge=OWNS)
    await app.connect(track, edge=CONTAINS)
    return owner, member, ws, app, track


@pytest.mark.asyncio
async def test_owner_governance_mismatch():
    owner, member, ws, app, track = await fixture_graph()
    ctx = await owner.get_context()
    for e in await ctx.find_edges_between(owner.id, app.id, edge_class=OWNS):
        await e.delete()
    await member.connect(app, edge=OWNS)
    assert await perms.resolve_role(owner.id, "app", app.id) is None


@pytest.mark.asyncio
async def test_exclusion_storage_error_allows_inheritance(monkeypatch):
    owner, member, ws, app, track = await fixture_graph()
    await owner.connect(app, edge=COLLABORATES_ON, role="owner")
    await member.connect(app, edge=COLLABORATES_ON, role="editor")
    await member.connect(track, edge=EXCLUDED_FROM)
    assert await perms.resolve_role(member.id, "track", track.id) is None
    ctx = await member.get_context()
    original = type(ctx).find_edges_between

    async def fault(self, *args, **kwargs):
        if kwargs.get("edge_class") == EXCLUDED_FROM:
            raise RuntimeError("audit injected exclusion lookup failure")
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(type(ctx), "find_edges_between", fault)
    assert await perms.resolve_role(member.id, "track", track.id) == "editor"


@pytest.mark.asyncio
async def test_missing_workspace_anchor_allows_direct_grant():
    owner = await User.create(user_id="audit-unscoped")
    track = await Track.create(title="Unscoped", workspace_id="")
    await owner.connect(track, edge=OWNS)
    assert await perms.resolve_role(owner.id, "track", track.id) == "owner"


@pytest.mark.asyncio
async def test_policy_cache_ignores_scope_and_tags(monkeypatch):
    policy = SimpleNamespace(
        id="audit-policy",
        is_active=True,
        scope="track:allowed",
        actions=["entry.read"],
        entry_types=[],
        tags=["allowed"],
        requires_human_approval=False,
    )
    monkeypatch.setattr(
        pe, "_find_policies_for_subject", AsyncMock(return_value=[policy])
    )
    subject = Subject(kind="agent", id="audit-agent")
    with isolated_permissions_cache():
        first = await pe.evaluate(
            subject=subject,
            action="entry.read",
            resource=Resource(
                kind="entry", id="same", scope="track:allowed", tags=["allowed"]
            ),
        )
        second = await pe.evaluate(
            subject=subject,
            action="entry.read",
            resource=Resource(
                kind="entry", id="same", scope="track:denied", tags=["denied"]
            ),
        )
        fresh = await pe.evaluate(
            subject=subject,
            action="entry.read",
            resource=Resource(
                kind="entry", id="same", scope="track:denied", tags=["denied"]
            ),
            _dry_run=True,
        )
        assert first.allowed and second.allowed and not fresh.allowed


@pytest.mark.asyncio
async def test_ownership_transfer_leaves_warm_owner_cache(monkeypatch):
    from app.services.ownership_transfer import transfer_track_ownership

    owner, member, ws, app, track = await fixture_graph()
    await add_collaborator(owner.id, "track", track.id, member.id, "editor")
    pc.clear_all()
    monkeypatch.setattr(pc, "_ENABLED", True)
    try:
        assert await perms.resolve_role(owner.id, "track", track.id) == "owner"
        await transfer_track_ownership(
            track_id=track.id, acting_user_id=owner.id, new_owner_user_id=member.id
        )
        assert await perms.resolve_role(owner.id, "track", track.id) == "owner"
        pc.clear_all()
        assert await perms.resolve_role(owner.id, "track", track.id) == "editor"
    finally:
        pc.clear_all()


@pytest.mark.asyncio
async def test_share_redeem_overrides_exclusion_and_link_revoke_retains_access():
    owner, member, ws, app, track = await fixture_graph()
    await member.connect(app, edge=COLLABORATES_ON, role="viewer")
    await add_exclusion(owner.id, "track", track.id, member.id)
    assert await perms.resolve_role(member.id, "track", track.id) is None
    link = await mint_share_link(owner.id, "track", track.id, "editor")
    await redeem_share_link(member.id, link["token"])
    assert await perms.resolve_role(member.id, "track", track.id) == "editor"
    await revoke_share_link(owner.id, link["share_link"]["id"])
    assert await perms.resolve_role(member.id, "track", track.id) == "editor"


@pytest.mark.asyncio
async def test_stale_inviter_still_confers_resource_grant(monkeypatch):
    from jvspatial.api.auth.models import User as AuthUser

    from app.services.invitations import (
        consume_invitation_token,
        create_resource_invitation,
    )

    owner, member, ws, app, track = await fixture_graph()
    auth = await AuthUser.create(
        email="audit-invitee@example.com", password_hash="test-only", name="Invitee"
    )
    recipient = await User.create(user_id=auth.id)
    await catalog_user(recipient)
    await add_collaborator(owner.id, "track", track.id, member.id, "admin")
    invitation, token, _ = await create_resource_invitation(
        resource_type="track",
        resource_id=track.id,
        workspace_id=ws.id,
        inviter_user_id=member.id,
        email=auth.email,
        role="editor",
        send_email_notification=False,
    )
    await remove_collaborator(owner.id, "track", track.id, member.id)
    assert await perms.resolve_role(member.id, "track", track.id) is None
    accepted, err = await consume_invitation_token(token, recipient.id)
    assert err is None
    assert await perms.resolve_role(recipient.id, "track", track.id) == "editor"


@pytest.mark.asyncio
async def test_collaborator_owner_can_delete_but_cannot_transfer():
    from app.services.ownership_transfer import transfer_track_ownership

    owner, member, ws, app, track = await fixture_graph()
    await add_collaborator(owner.id, "track", track.id, member.id, "owner")
    assert await perms.can_delete_track(member.id, track.id)
    from app.api.errors import BadRequestError

    with pytest.raises(BadRequestError):
        await transfer_track_ownership(
            track_id=track.id, acting_user_id=member.id, new_owner_user_id=owner.id
        )


@pytest.mark.asyncio
async def test_viewer_author_can_edit_own_entry():
    owner, member, ws, app, track = await fixture_graph()
    await add_collaborator(owner.id, "track", track.id, member.id, "viewer")
    entry = await Entry.create(title="Author", track_id=track.id, author_id=member.id)
    await track.connect(entry, edge=CONTAINS)
    assert await perms.can_edit_entry(member.id, entry.id)


@pytest.mark.asyncio
async def test_avatar_non_member_truthiness(
    monkeypatch, authenticated_client, second_user_client
):
    from io import BytesIO

    from PIL import Image

    response = await authenticated_client.post(
        "/api/workspaces", json={"name": "Audit Avatar"}
    )
    assert response.status_code == 200, response.text
    wid = response.json()["workspace"]["id"]
    data = BytesIO()
    Image.new("RGB", (32, 32), "red").save(data, format="PNG")
    upload = await authenticated_client.post(
        f"/api/workspaces/{wid}/avatar",
        files={"file": ("logo.png", data.getvalue(), "image/png")},
    )
    assert upload.status_code == 200, upload.text
    fetched = await second_user_client.get(f"/api/workspaces/{wid}/avatar?size=128")
    assert fetched.status_code == 200, fetched.text


@pytest.mark.asyncio
async def test_policy_authority_scope_is_not_intersected(monkeypatch):
    policy = SimpleNamespace(
        id="audit-unbounded",
        is_active=True,
        scope="*",
        actions=["entry.*"],
        entry_types=[],
        tags=[],
        requires_human_approval=False,
    )
    monkeypatch.setattr(
        pe, "_find_policies_for_subject", AsyncMock(return_value=[policy])
    )
    decision = await pe.evaluate(
        subject=Subject(kind="connector", id="audit-owned-connector"),
        action="entry.update",
        resource=Resource(
            kind="entry", id="foreign-private-entry", scope="track:foreign-workspace"
        ),
        _dry_run=True,
    )
    assert decision.allowed


@pytest.mark.asyncio
async def test_facade_point_read_crosses_bound_workspace():
    from app.services.hooks.registry import ToolContext

    owner, member, ws, app, track = await fixture_graph()
    foreign = await Workspace.create(name="Other", kind="organization")
    await catalog_workspace(foreign)
    await owner.connect(foreign, edge=IS_MEMBER_OF, role="owner")
    foreign_track = await Track.create(title="Other Track", workspace_id=foreign.id)
    await catalog_track(foreign_track)
    await owner.connect(foreign_track, edge=OWNS)
    entry = await Entry.create(title="Other Entry", track_id=foreign_track.id)
    await foreign_track.connect(entry, edge=CONTAINS)
    context = ToolContext(user_id=owner.id, workspace_id=ws.id, scope=f"app:{app.id}")
    assert (await context.get_entry(entry.id)).id == entry.id
    result = await context.get({"kind": "entry", "id": entry.id})
    assert result["workspace_id"] == ws.id
    assert (await context.find_entries({"id": entry.id})) == []


@pytest.mark.asyncio
async def test_invite_accept_can_win_after_revoke(monkeypatch):
    from jvspatial.api.auth.models import User as AuthUser

    from app.services import invitations as inv

    owner, member, ws, app, track = await fixture_graph()
    auth = await AuthUser.create(
        email="audit-race@example.com", password_hash="test-only", name="Invitee"
    )
    recipient = await User.create(user_id=auth.id)
    await catalog_user(recipient)
    invitation, token, _ = await inv.create_resource_invitation(
        resource_type="track",
        resource_id=track.id,
        workspace_id=ws.id,
        inviter_user_id=owner.id,
        email=auth.email,
        role="viewer",
        send_email_notification=False,
    )
    original = inv._materialize_invitation_grants

    async def revoke_during_accept(user, observed):
        current = await inv.Invitation.get(observed.id)
        await inv.revoke_invitation(current, actor_user_id=owner.id)
        return await original(user, observed)

    monkeypatch.setattr(inv, "_materialize_invitation_grants", revoke_during_accept)
    accepted, error = await inv.consume_invitation_token(token, recipient.id)
    assert error is None and accepted.status == "accepted"
    assert await perms.resolve_role(recipient.id, "track", track.id) == "viewer"


@pytest.mark.asyncio
async def test_removed_member_can_rejoin_via_existing_share_link():
    from app.services.workspace_departure import remove_workspace_membership

    owner, member, ws, app, track = await fixture_graph()
    link = await mint_share_link(owner.id, "track", track.id, "viewer")
    await remove_workspace_membership(member=member, workspace_id=ws.id)
    assert await perms.resolve_role(member.id, "track", track.id) is None
    await redeem_share_link(member.id, link["token"])
    assert await perms.resolve_role(member.id, "track", track.id) == "viewer"


@pytest.mark.asyncio
async def test_workspace_admin_self_assigns_creation_rights(
    authenticated_client, second_user_client, second_user
):
    response = await authenticated_client.post(
        "/api/workspaces", json={"name": "Audit Admin"}
    )
    assert response.status_code == 200, response.text
    wid = response.json()["workspace"]["id"]
    added = await authenticated_client.post(
        f"/api/workspaces/{wid}/members",
        json={
            "member_user_id": second_user.id,
            "role": "admin",
            "can_create_apps": False,
            "can_create_tracks": False,
        },
    )
    assert added.status_code == 200, added.text
    changed = await second_user_client.patch(
        f"/api/workspaces/{wid}/members/{second_user.id}",
        json={"can_create_apps": True, "can_create_tracks": True},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["can_create_apps"] and changed.json()["can_create_tracks"]


@pytest.mark.asyncio
async def test_viewer_legacy_collaborators_enumerates_other_people(
    authenticated_client, second_user_client, second_user
):
    response = await authenticated_client.post(
        "/api/apps", json={"name": "Audit Directory"}
    )
    assert response.status_code == 200, response.text
    aid = response.json()["app"]["id"]
    added = await authenticated_client.post(
        f"/api/apps/{aid}/collaborators",
        json={"collaborator_user_id": second_user.id, "role": "viewer"},
    )
    assert added.status_code == 200, added.text
    snapshot = await second_user_client.get(f"/api/apps/{aid}/access")
    assert snapshot.status_code == 200 and "direct" not in snapshot.json()
    legacy = await second_user_client.get(f"/api/apps/{aid}/collaborators")
    assert legacy.status_code == 200 and len(legacy.json()["collaborators"]) >= 2


@pytest.mark.asyncio
async def test_unverified_email_can_discover_and_accept_invitation_without_token(
    client, authenticated_client
):
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    email = "audit-unverified@example.com"
    signup = await client.post(
        "/api/auth/signup",
        json={"email": email, "password": "Audit-Password-123!", "name": "Unverified"},
    )
    assert signup.status_code in (200, 201), signup.text
    token = signup.json()["access_token"]
    ws = await authenticated_client.post(
        "/api/workspaces", json={"name": "Audit Unverified Invite"}
    )
    assert ws.status_code == 200, ws.text
    wid = ws.json()["workspace"]["id"]
    invited = await authenticated_client.post(
        f"/api/workspaces/{wid}/invitations", json={"email": email, "role": "admin"}
    )
    assert invited.status_code == 200, invited.text
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as unverified:
        me = await unverified.get("/api/auth/me")
        assert me.status_code == 200, me.text
        assert me.json()["user"]["email"] == email
        assert not me.json()["user"].get("email_verified", False)
        pending = await unverified.get("/api/me/invitations")
        assert pending.status_code == 200, pending.text
        iid = next(
            i["id"] for i in pending.json()["invitations"] if i["workspace_id"] == wid
        )
        accepted = await unverified.post(f"/api/me/invitations/{iid}/accept")
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["role"] == "admin"
        from app.services.workspace_permissions import can_access_workspace

        assert await can_access_workspace(me.json()["user"]["id"], wid) == "admin"


@pytest.mark.asyncio
async def test_event_subscription_decision_cache_crosses_track_scope():
    from app.services.event_subscription_registry import _user_permitted_for_scope

    owner, member, ws, app, track = await fixture_graph()
    await member.connect(track, edge=COLLABORATES_ON, role="viewer")
    denied = await Track.create(
        title="Denied", workspace_id=ws.id, visibility="private"
    )
    await catalog_track(denied)
    await owner.connect(denied, edge=OWNS)
    with isolated_permissions_cache():
        assert await _user_permitted_for_scope(member.id, f"track:{track.id}")
        assert await _user_permitted_for_scope(member.id, f"track:{denied.id}")
    with isolated_permissions_cache():
        assert not await _user_permitted_for_scope(member.id, f"track:{denied.id}")


@pytest.mark.asyncio
async def test_entry_exclusion_does_not_filter_event_payload(monkeypatch):
    import json

    from app.services import event_subscription_registry as events
    from app.services.change_event_logger import ChangeEventEnvelope

    owner, member, ws, app, track = await fixture_graph()
    await member.connect(track, edge=COLLABORATES_ON, role="viewer")
    entry = await Entry.create(title="Restricted", track_id=track.id)
    await track.connect(entry, edge=CONTAINS)
    await member.connect(entry, edge=EXCLUDED_FROM)
    assert await perms.resolve_role(member.id, "entry", entry.id) is None
    socket = SimpleNamespace(send_text=AsyncMock())
    monkeypatch.setattr(events, "_subscriptions", {member.id: [socket]})
    monkeypatch.setattr(events, "_consumer_hooks", [])
    monkeypatch.setattr(events, "is_change_event_enabled", lambda: True)
    event = ChangeEventEnvelope(
        ts="2026-10-10T00:00:00+00:00",
        actor_kind="human",
        actor_id=owner.id,
        action="entry.update",
        resource_type="Entry",
        resource_id=entry.id,
        scope=f"track:{track.id}",
        after={"title": "restricted audit payload"},
    )
    with isolated_permissions_cache():
        await events.broadcast_change_event(event)
    socket.send_text.assert_awaited_once()
    assert (
        json.loads(socket.send_text.call_args.args[0])["payload"]["after"]["title"]
        == "restricted audit payload"
    )


@pytest.mark.asyncio
async def test_transfer_write_failure_leaves_no_owns_edge(monkeypatch):
    from app.services.ownership_transfer import transfer_track_ownership

    owner, member, ws, app, track = await fixture_graph()
    await add_collaborator(owner.id, "track", track.id, member.id, "editor")
    original = User.connect

    async def fault(self, target, *args, **kwargs):
        if self.id == member.id and kwargs.get("edge") == OWNS:
            raise RuntimeError("audit injected ownership wire failure")
        return await original(self, target, *args, **kwargs)

    monkeypatch.setattr(User, "connect", fault)
    with pytest.raises(RuntimeError, match="ownership wire failure"):
        await transfer_track_ownership(
            track_id=track.id, acting_user_id=owner.id, new_owner_user_id=member.id
        )
    context = await owner.get_context()
    assert not await context.find_edges_between(None, track.id, edge_class=OWNS)
    assert await perms.resolve_role(owner.id, "track", track.id) == "editor"


@pytest.mark.asyncio
async def test_read_operation_replays_entry_after_exclusion(monkeypatch):
    from app.api.errors import InsufficientPermissionsError
    from app.services.app_operations import dispatch, registry
    from app.services.app_operations.idempotency import (
        clear_idempotency_memory_for_tests,
    )

    owner, member, ws, app, track = await fixture_graph()
    await add_collaborator(owner.id, "app", app.id, member.id, "viewer")
    entry = await Entry.create(title="Sensitive result", track_id=track.id)
    await track.connect(entry, edge=CONTAINS)
    await owner.connect(entry, edge=COLLABORATES_ON, role="owner")
    monkeypatch.setattr(registry, "_OPERATIONS", {})
    registry.register_app_operations(
        ws.id,
        app.id,
        "review-fixture",
        [
            {
                "key": "read_record",
                "kind": "read",
                "policy_action": "app.read",
                "handler_ref": "review:read_record",
            }
        ],
    )
    calls = []

    async def handler(body, context):
        calls.append(entry.id)
        if not await perms.can_view_entry(context.user_id, entry.id):
            raise InsufficientPermissionsError(message="Entry access denied")
        return {
            "title": entry.title,
            "object_refs": [{"kind": "entry", "id": entry.id}],
        }

    # Only substitute the App implementation; dispatch, policy, reference
    # validation and the idempotency store remain real.
    monkeypatch.setattr(dispatch, "resolve_handler", lambda _: handler)
    clear_idempotency_memory_for_tests()
    arguments = dict(
        user_id=member.id,
        workspace_id=ws.id,
        app_id=app.id,
        operation_key="read_record",
        payload={},
    )
    try:
        with isolated_permissions_cache():
            first = await dispatch.invoke_app_operation(
                **arguments, idempotency_key="old"
            )
        await add_exclusion(owner.id, "entry", entry.id, member.id)
        with isolated_permissions_cache():
            assert not await perms.can_view_entry(member.id, entry.id)
            repeated = await dispatch.invoke_app_operation(
                **arguments, idempotency_key="old"
            )
        assert repeated == first
        assert repeated["output"]["title"] == "Sensitive result"
        assert len(calls) == 1
        with isolated_permissions_cache():
            with pytest.raises(InsufficientPermissionsError):
                await dispatch.invoke_app_operation(
                    **arguments, idempotency_key="fresh"
                )
        assert len(calls) == 2
    finally:
        clear_idempotency_memory_for_tests()


@pytest.mark.asyncio
async def test_accepted_invitation_reports_role_not_materialized():
    from jvspatial.api.auth.models import User as AuthUser

    from app.services.invitations import (
        consume_invitation_token,
        create_resource_invitation,
    )

    owner, member, ws, app, track = await fixture_graph()
    auth = await AuthUser.create(
        email="audit-existing@example.com", password_hash="test-only"
    )
    recipient = await User.create(user_id=auth.id)
    await catalog_user(recipient)
    await add_collaborator(owner.id, "track", track.id, recipient.id, "viewer")
    invitation, token, _ = await create_resource_invitation(
        resource_type="track",
        resource_id=track.id,
        workspace_id=ws.id,
        inviter_user_id=owner.id,
        email=auth.email,
        role="editor",
        send_email_notification=False,
    )
    accepted, error = await consume_invitation_token(token, recipient.id)
    assert error is None and accepted.status == "accepted"
    assert accepted.target_resource_role == "editor"
    assert await perms.resolve_role(recipient.id, "track", track.id) == "viewer"


@pytest.mark.asyncio
async def test_direct_viewer_suppresses_inherited_editor():
    owner, member, ws, app, track = await fixture_graph()
    await add_collaborator(owner.id, "app", app.id, member.id, "editor")
    assert await perms.resolve_role(member.id, "track", track.id) == "editor"
    await add_collaborator(owner.id, "track", track.id, member.id, "viewer")
    assert await perms.resolve_role(member.id, "track", track.id) == "viewer"
