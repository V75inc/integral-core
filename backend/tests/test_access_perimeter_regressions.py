"""Secure acceptance for access-review perimeter findings (not characterization)."""

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
from app.services import policy_engine as pe
from app.services.app_graph import (
    catalog_app,
    catalog_track,
    catalog_user,
    catalog_workspace,
)

pytestmark = pytest.mark.smoke


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong", [None, "owner", "actor", "scope", "action"])
async def test_deleted_connector_requires_recorded_personal_owner(wrong):
    from app.services.change_event_logger import ChangeEventEnvelope
    from app.services.event_access import can_release_event

    _, caller, _, _, _, _ = await rooted_graph()
    event = ChangeEventEnvelope(
        ts="2026-10-10T00:00:00+00:00",
        actor_kind="human",
        actor_id=caller.id,
        action="connector.delete",
        resource_type="Connector",
        resource_id="n.Connector.deleted",
        scope=f"user:{caller.id}",
        before={"owner": caller.id, "kind": "custom"},
    )
    if wrong == "owner":
        event.before["owner"] = "another-user"
    elif wrong == "actor":
        event.actor_id = "another-user"
    elif wrong == "scope":
        event.scope = "user:another-user"
    elif wrong == "action":
        event.action = "connector.update"
    assert await can_release_event(
        caller.id, event, action="audit_log.read", evaluate=pe.evaluate
    ) is (wrong is None)


@pytest.mark.asyncio
async def test_denied_create_forensics_require_scope_administration():
    from app.services.change_event_logger import ChangeEventEnvelope
    from app.services.event_access import can_release_event

    owner, reader, _, _, track, _ = await rooted_graph()
    event = ChangeEventEnvelope(
        ts="2026-10-10T00:00:00+00:00",
        actor_kind="agent",
        actor_id="unpolicied-agent",
        action="policy.deny",
        resource_type="entry",
        resource_id="entry-never-created",
        scope=f"track:{track.id}",
        details={"failed_action": "entry.create", "decision_reason": "denied"},
    )
    assert await can_release_event(
        owner.id, event, action="audit_log.read", evaluate=pe.evaluate
    )
    assert not await can_release_event(
        reader.id, event, action="audit_log.read", evaluate=pe.evaluate
    )
    event.after = {"title": "not forensic metadata"}
    assert not await can_release_event(
        owner.id, event, action="audit_log.read", evaluate=pe.evaluate
    )


@pytest.mark.asyncio
async def test_model_event_checks_actual_attached_parent_administration():
    from app.services.change_event_logger import ChangeEventEnvelope
    from app.services.event_access import can_release_event

    owner, reader, _, _, track, _ = await rooted_graph()
    event = ChangeEventEnvelope(
        ts="2026-10-10T00:00:00+00:00",
        actor_kind="human",
        actor_id=owner.id,
        action="operational_model.update",
        resource_type="OperationalModel",
        resource_id=track.attached_operational_model_id,
        scope=f"track:{track.id}",
        after={"manifest": {}},
    )
    assert await can_release_event(
        owner.id, event, action="audit_log.read", evaluate=pe.evaluate
    )
    assert not await can_release_event(
        reader.id, event, action="audit_log.read", evaluate=pe.evaluate
    )
    event.scope = f"user:{owner.id}"
    assert not await can_release_event(
        owner.id, event, action="audit_log.read", evaluate=pe.evaluate
    )


@pytest.mark.asyncio
async def test_policy_event_requires_creator_and_personal_actor_scope():
    from app.services.change_event_logger import ChangeEventEnvelope
    from app.services.event_access import can_release_event
    from app.services.policy_registry import create_policy

    owner, reader, _, _, _, _ = await rooted_graph()
    policy = await create_policy(
        subject_kind="human", subject_id=owner.id, created_by=owner.id
    )
    event = ChangeEventEnvelope(
        ts="2026-10-10T00:00:00+00:00",
        actor_kind="human",
        actor_id=owner.id,
        action="policy.create",
        resource_type="Policy",
        resource_id=policy.id,
        scope=f"user:{owner.id}",
        after={"actions": []},
    )
    assert await can_release_event(
        owner.id, event, action="audit_log.read", evaluate=pe.evaluate
    )
    event.scope = f"user:{reader.id}"
    event.actor_id = reader.id
    assert not await can_release_event(
        reader.id, event, action="audit_log.read", evaluate=pe.evaluate
    )


async def rooted_graph(reader_user=None):
    owner = await User.create(user_id="perimeter-owner")
    await catalog_user(owner)
    reader = reader_user or await User.create(user_id="perimeter-reader")
    await catalog_user(reader)
    workspace = await Workspace.create(name="Perimeter", kind="organization")
    await catalog_workspace(workspace)
    await owner.connect(workspace, edge=IS_MEMBER_OF, role="owner")
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    app = await App.create(
        name="Perimeter", workspace_id=workspace.id, visibility="private"
    )
    await catalog_app(app)
    await owner.connect(app, edge=OWNS)
    await reader.connect(app, edge=COLLABORATES_ON, role="viewer")
    track = await Track.create(
        title="Perimeter", workspace_id=workspace.id, visibility="inherit"
    )
    await catalog_track(track)
    await owner.connect(track, edge=OWNS)
    await app.connect(track, edge=CONTAINS)
    entry = await Entry.create(title="Restricted content", track_id=track.id)
    await track.connect(entry, edge=CONTAINS)
    await owner.connect(entry, edge=COLLABORATES_ON, role="owner")
    return owner, reader, workspace, app, track, entry


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["scope", "tags", "entry_type"])
async def test_policy_memo_distinguishes_complete_resource(changed, monkeypatch):
    owner, agent, policy, track, entry = await rooted_policy_fixture(
        entry_types=["allowed"], tags=["allowed"]
    )
    subject = Subject(kind="agent", id=agent.id)
    resource = Resource(
        kind="entry",
        id=entry.id,
        scope=f"track:{track.id}",
        tags=["allowed"],
        entry_type="allowed",
    )
    denied = resource.model_copy(
        update={changed: ["denied"] if changed == "tags" else "denied"}
    )
    with isolated_permissions_cache():
        assert (
            await pe.evaluate(subject=subject, action="entry.read", resource=resource)
        ).allowed
        assert not (
            await pe.evaluate(subject=subject, action="entry.read", resource=denied)
        ).allowed


@pytest.mark.asyncio
async def test_write_policy_is_not_memoized_across_payloads(monkeypatch):
    evaluator = AsyncMock(return_value=True)
    monkeypatch.setattr(pe, "_evaluate_human_default", evaluator)
    with isolated_permissions_cache():
        for title in ("first", "second"):
            result = await pe.evaluate(
                subject=Subject(kind="human", id="caller"),
                action="entry.update",
                resource=Resource(kind="entry", id="same", scope="track:t"),
                payload={"title": title},
            )
            assert result.allowed
    assert evaluator.await_count == 2


@pytest.mark.asyncio
async def test_exclusion_lookup_failure_cannot_restore_inheritance(monkeypatch):
    from app.exceptions import AuthorizationUnavailableError

    _, reader, _, _, _, entry = await rooted_graph()
    with isolated_permissions_cache():
        assert await perms.can_view_entry(reader.id, entry.id)
    await reader.connect(entry, edge=EXCLUDED_FROM)
    with isolated_permissions_cache():
        assert not await perms.can_view_entry(reader.id, entry.id)

    async def fault(*args, **kwargs):
        raise RuntimeError("exclusion store unavailable")

    monkeypatch.setattr(EXCLUDED_FROM, "count", fault)
    with isolated_permissions_cache(), pytest.raises(AuthorizationUnavailableError):
        await perms.can_view_entry(reader.id, entry.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope_kind", ["track", "user"])
async def test_live_event_rechecks_entry_and_revocation(monkeypatch, scope_kind):
    from app.services import event_subscription_registry as registry
    from app.services.change_event_logger import ChangeEventEnvelope

    _, reader, _, _, track, entry = await rooted_graph()
    event = ChangeEventEnvelope(
        ts="2026-10-10T00:00:00+00:00",
        actor_kind="human",
        actor_id="actor",
        action="entry.update",
        resource_type="Entry",
        resource_id=entry.id,
        scope=f"track:{track.id}" if scope_kind == "track" else f"user:{reader.id}",
        after={"title": entry.title},
    )
    monkeypatch.setattr(registry, "_subscriptions", {})
    socket = AsyncMock()
    await registry.register_subscription(reader.id, socket)
    await registry.broadcast_change_event(event)
    assert socket.send_text.await_count == 1
    await reader.connect(entry, edge=EXCLUDED_FROM)
    # Deliberately retain the caller's context cache between messages.
    await registry.broadcast_change_event(event)
    assert socket.send_text.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["audit", "poll"])
async def test_stored_event_surfaces_filter_excluded_entry(surface, monkeypatch):
    from starlette.requests import Request

    from app.api import audit_log, events_polling
    from app.services import event_history
    from app.services.change_event_logger import ChangeEventEnvelope

    _, reader, workspace, _, track, entry = await rooted_graph()
    event = ChangeEventEnvelope(
        id="event-1",
        ts="2026-10-10T00:00:00+00:00",
        actor_kind="human",
        actor_id="actor",
        action="entry.update",
        resource_type="Entry",
        resource_id=entry.id,
        scope=f"track:{track.id}",
        after={"title": entry.title},
    )
    module = audit_log if surface == "audit" else events_polling

    async def find_page(**kwargs):
        return [event] if kwargs.get("cursor") is None else []

    monkeypatch.setattr(
        module,
        "get_change_event_logger",
        lambda: SimpleNamespace(find_page=find_page),
    )
    monkeypatch.setattr(event_history, "envelope_from_dblog", lambda row: row)
    request = Request(
        {
            "type": "http",
            "headers": [(b"x-integral-scope", f"ws:{workspace.id}".encode())],
            "state": {"user": {"id": reader.id}},
        }
    )
    handler = (
        module.list_audit_log if surface == "audit" else module.list_events_polling
    )
    assert len((await handler(request))["events"]) == 1
    await reader.connect(entry, edge=EXCLUDED_FROM)
    assert (await handler(request))["events"] == []


@pytest.mark.asyncio
async def test_read_replay_withholds_revoked_record_without_rerunning_handler(
    monkeypatch,
    test_user,
):
    from app.api.errors import InsufficientPermissionsError
    from app.services.app_operations import dispatch, registry
    from app.services.app_operations.idempotency import (
        clear_idempotency_memory_for_tests,
    )

    profile = await perms.get_user_node(test_user.id)
    if profile is None:
        profile = await User.create(user_id=test_user.id)
    _, reader, workspace, app, _, entry = await rooted_graph(reader_user=profile)
    monkeypatch.setattr(registry, "_OPERATIONS", {})
    registry.register_app_operations(
        workspace.id,
        app.id,
        "fixture",
        [
            {
                "key": "read",
                "kind": "read",
                "policy_action": "app.read",
                "handler_ref": "fixture:read",
            }
        ],
    )
    handler = AsyncMock(
        return_value={
            "title": entry.title,
            "object_refs": [{"kind": "entry", "id": entry.id}],
        }
    )
    monkeypatch.setattr(dispatch, "resolve_handler", lambda _: handler)
    arguments = dict(
        user_id=reader.id,
        workspace_id=workspace.id,
        app_id=app.id,
        operation_key="read",
        idempotency_key="same",
    )
    clear_idempotency_memory_for_tests()
    try:
        with isolated_permissions_cache():
            first = await dispatch.invoke_app_operation(**arguments)
        with isolated_permissions_cache():
            assert await dispatch.invoke_app_operation(**arguments) == first
        assert handler.await_count == 1
        await reader.connect(entry, edge=EXCLUDED_FROM)
        with pytest.raises(InsufficientPermissionsError):
            await dispatch.invoke_app_operation(**arguments)
        assert handler.await_count == 1
    finally:
        clear_idempotency_memory_for_tests()


def test_event_wire_scrubs_nested_mailbox_and_reset_credentials():
    from app.services.event_access import scrub_event_credentials

    snapshot = {
        "context": {
            "preferences": {
                "email_verification": {"hash": "otp"},
                "reset_token": {"hash": "reset"},
                "theme": "dark",
            }
        },
        "password_hash": "secret",
        "display_name": "Reader",
    }
    assert scrub_event_credentials(snapshot) == {
        "context": {"preferences": {"theme": "dark"}},
        "display_name": "Reader",
    }
    assert snapshot["password_hash"] == "secret"


@pytest.mark.asyncio
async def test_workspace_avatar_denies_outsider_before_storage_read(monkeypatch):
    from starlette.requests import Request

    from app.api import workspaces
    from app.api.errors import InsufficientPermissionsError

    _, reader, workspace, _, _, _ = await rooted_graph()
    outsider = await User.create(user_id="perimeter-outsider")
    await catalog_user(outsider)
    workspace.avatar_attachment_id = "avatar-fixture"
    await workspace.save()
    storage = SimpleNamespace(read_attachment=AsyncMock(return_value=b"png-fixture"))
    monkeypatch.setattr(
        workspaces,
        "_find_workspace_avatar_variant",
        AsyncMock(return_value=SimpleNamespace(storage_key="avatar")),
    )
    monkeypatch.setattr(workspaces, "get_attachment_storage_service", lambda: storage)

    def request_for(user):
        return Request(
            {
                "type": "http",
                "headers": [],
                "query_string": b"",
                "state": {"user": {"id": user.id}},
            }
        )

    with pytest.raises(InsufficientPermissionsError):
        await workspaces.get_workspace_avatar(request_for(outsider), workspace.id)
    storage.read_attachment.assert_not_awaited()
    response = await workspaces.get_workspace_avatar(request_for(reader), workspace.id)
    assert response.body == b"png-fixture"
    assert storage.read_attachment.await_count == 1


@pytest.mark.asyncio
async def test_policy_reduction_is_seen_within_same_request(monkeypatch):
    _, agent, policy, track, entry = await rooted_policy_fixture()
    with isolated_permissions_cache():
        args = dict(
            subject=Subject(kind="agent", id=agent.id),
            action="entry.read",
            resource=Resource(kind="entry", id=entry.id, scope=f"track:{track.id}"),
        )
        assert (await pe.evaluate(**args)).allowed
        policy.actions = []
        await policy.save()
        assert not (await pe.evaluate(**args)).allowed


async def rooted_policy_fixture(*, entry_types=None, tags=None):
    """Use primary rooted subject/grant facts for memo and reduction assertions."""
    from jvspatial.api.auth.models import User as AuthUser

    from app.agentive.edges import HAS_ORG_AGENT
    from app.agentive.nodes import AgentConfig
    from app.services.app_graph import ensure_integral_app_graph
    from app.services.policy_registry import create_policy

    await ensure_integral_app_graph(include_library=False)
    owner, _, workspace, _, track, entry = await rooted_graph()
    identity = await AuthUser.create(
        email=f"perimeter-{owner.id}@example.com",
        password_hash="fixture",
        roles=["user"],
        is_active=True,
    )
    owner.user_id = identity.id
    owner.email_verified = True
    owner.verified_email = identity.email
    await owner.save()
    agent = await AgentConfig.create(
        scope="org_facing",
        facet="org_facing",
        workspace_id=workspace.id,
        is_active=True,
    )
    await workspace.connect(agent, edge=HAS_ORG_AGENT)
    policy = await create_policy(
        subject_kind="agent",
        subject_id=agent.id,
        scope=f"track:{track.id}",
        actions=["entry.read"],
        created_by=owner.id,
        entry_types=entry_types or [],
        tags=tags or [],
    )
    return owner, agent, policy, track, entry
