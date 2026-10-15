"""Actual current ACLs govern embedded event references on both transports."""

import json
from unittest.mock import AsyncMock

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.models.edges import (
    COLLABORATES_ON,
    CONTAINS,
    EXCLUDED_FROM,
    IS_MEMBER_OF,
    OWNS,
)
from app.models.nodes import Entry, Track
from app.services.app_graph import catalog_track
from app.services.change_event_logger import ChangeEventEnvelope
from app.services.event_access import prepare_event_payload
from app.services.policy_engine import evaluate

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def event_fixture():
    import uuid

    from jvspatial.api.auth.models import User as AuthUser

    from app.models.nodes import User
    from app.services.app_graph import catalog_user
    from app.services.resource_location import locate_resource
    from tests.contract.test_staging_command_apply_postgres import proposal

    auth, workspace, track, _ = await proposal()
    owner = (await User.find({"user_id": auth.id}))[0]
    location = await locate_resource("track", track.id)
    from app.models.nodes import App

    app = await App.get(location.app_id)
    reader_auth = await AuthUser.create(
        email=f"event-{uuid.uuid4().hex}@example.com", password_hash="fixture"
    )
    reader = await User.create(user_id=reader_auth.id, display_name="Event reader")
    await catalog_user(reader)
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    await reader.connect(app, edge=COLLABORATES_ON, role="viewer")
    entry = await Entry.create(title="Visible", track_id=track.id)
    await track.connect(entry, edge=CONTAINS)
    private = await Track.create(title="Private", workspace_id=workspace.id)
    await catalog_track(private)
    await app.connect(private, edge=CONTAINS)
    await owner.connect(private, edge=OWNS)
    await reader.connect(private, edge=EXCLUDED_FROM)
    secret = await Entry.create(title="Secret client", track_id=private.id)
    await private.connect(secret, edge=CONTAINS)
    event = ChangeEventEnvelope(
        id="projection-probe",
        ts="2026-10-14T00:00:00Z",
        actor_kind="human",
        actor_id=owner.id,
        action="entry.update",
        resource_type="Entry",
        resource_id=entry.id,
        scope=f"track:{track.id}",
        before={"id": entry.id, "title": "Visible"},
        after={
            "id": entry.id,
            "custom_fields": {"reference": {"id": secret.id, "label": secret.title}},
        },
        details={"reason": "Updated"},
    )
    return owner, reader, workspace, secret, event


async def test_nested_private_reference_withholds_label_and_id(postgres_raw_db):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, reader, _, secret, event = await event_fixture()
        full = await prepare_event_payload(
            owner.id, event, action="audit_log.read", evaluate=evaluate
        )
        restricted = await prepare_event_payload(
            reader.id, event, action="audit_log.read", evaluate=evaluate, flat=True
        )
        assert secret.id in json.dumps(full)
        assert restricted["before"] == event.before
        assert restricted["after"] is None
        assert secret.id not in json.dumps(restricted)
        assert secret.title not in json.dumps(restricted)
        assert event.after is not None  # Never mutate the stored envelope.


async def test_broadcast_prepares_distinct_recipient_payloads(postgres_raw_db):
    from app.services import event_subscription_registry as registry

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, reader, _, secret, event = await event_fixture()
        first, second = AsyncMock(), AsyncMock()
        registry._subscriptions.clear()
        try:
            await registry.register_subscription(owner.id, first)
            await registry.register_subscription(reader.id, second)
            await registry.broadcast_change_event(event)
            assert secret.id in first.send_text.await_args.args[0]
            assert secret.id not in second.send_text.await_args.args[0]
            assert (
                json.loads(second.send_text.await_args.args[0])["payload"]["after"]
                is None
            )
        finally:
            registry._subscriptions.clear()


async def test_foreign_or_missing_reference_withheld_even_for_owner(postgres_raw_db):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, _, _, _, event = await event_fixture()
        from app.services.permissions import can_view_entry
        from tests.contract.test_staging_command_apply_postgres import proposal

        _, foreign_workspace, foreign_track, _ = await proposal()
        await owner.connect(foreign_workspace, edge=IS_MEMBER_OF, role="owner")
        foreign = await Entry.create(title="Foreign secret", track_id=foreign_track.id)
        await foreign_track.connect(foreign, edge=CONTAINS)
        await owner.connect(foreign, edge=COLLABORATES_ON, role="viewer")
        assert await can_view_entry(owner.id, foreign.id)
        # The owner can read both graphs; bound event workspace still wins.
        event.after = {"id": foreign.id, "title": foreign.title}
        payload = await prepare_event_payload(
            owner.id, event, action="audit_log.read", evaluate=evaluate
        )
        assert payload["after"] is None

        event.after = {"id": "n.Entry.missing", "title": "Deleted secret"}
        payload = await prepare_event_payload(
            owner.id, event, action="audit_log.read", evaluate=evaluate
        )
        assert payload["after"] is None


async def test_personal_connector_references_use_current_bound_workspace(
    postgres_raw_db,
):
    from app.agentive.services.connector_registry_node import create_connector

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, reader, workspace, secret, event = await event_fixture()
        connector = await create_connector(
            owner=reader.user_id, kind="custom", workspace_id=workspace.id
        )
        track_id = event.scope.partition(":")[2]
        event.resource_type, event.resource_id = "Connector", connector.id
        event.scope = f"user:{reader.user_id}"
        event.after = {"owner": reader.user_id, "bindings": [track_id]}
        payload = await prepare_event_payload(
            reader.user_id, event, action="audit_log.read", evaluate=evaluate
        )
        assert payload["after"] == event.after
        event.after = {
            "owner": reader.user_id,
            "bindings": [secret.id],
            "label": secret.title,
        }
        payload = await prepare_event_payload(
            reader.user_id, event, action="audit_log.read", evaluate=evaluate
        )
        assert payload["after"] is None


async def test_avatar_reference_requires_unique_own_avatar_edge(postgres_raw_db):
    from app.models.edges import HAS_ATTACHMENT
    from app.models.nodes import Attachment

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, reader, _, _, event = await event_fixture()
        attachment = await Attachment.create(
            filename="Avatar", uploaded_by=reader.user_id
        )
        await reader.connect(attachment, edge=HAS_ATTACHMENT, role="avatar")
        event.resource_type, event.resource_id = "User", reader.id
        event.scope = f"user:{reader.user_id}"
        event.after = {"avatar_attachment_id": attachment.id}
        payload = await prepare_event_payload(
            reader.user_id, event, action="audit_log.read", evaluate=evaluate
        )
        assert payload["after"] == event.after
        await owner.connect(attachment, edge=HAS_ATTACHMENT, role="avatar")
        payload = await prepare_event_payload(
            reader.user_id, event, action="audit_log.read", evaluate=evaluate
        )
        assert payload["after"] is None


async def test_connector_workspace_scalar_cannot_override_current_edge(postgres_raw_db):
    from app.agentive.services.connector_registry_node import create_connector
    from app.exceptions import AuthorizationUnavailableError

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, reader, workspace, _, event = await event_fixture()
        connector = await create_connector(
            owner=reader.user_id, kind="custom", workspace_id=workspace.id
        )
        event.resource_type, event.resource_id = "Connector", connector.id
        event.scope = f"user:{reader.user_id}"
        connector.workspace_id = "n.Workspace.inconsistent"
        await connector.save()
        with pytest.raises(AuthorizationUnavailableError):
            await prepare_event_payload(
                reader.user_id, event, action="audit_log.read", evaluate=evaluate
            )
