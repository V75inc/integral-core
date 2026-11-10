"""Resident assignment uses the canonical Entry lock and durable event outbox."""

import asyncio
from types import SimpleNamespace

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive.edges import ResidentAssignment
from app.agentive.services.work_events import declaration_matches
from app.api.errors import (
    InsufficientPermissionsError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.contracts.task_semantics import (
    RESIDENT_ASSIGNMENT_CHANGED,
    RESIDENT_ASSIGNMENT_SETTINGS_CHANGED,
)
from app.models.edges import CONTAINS, IS_MEMBER_OF, IS_OF_TYPE
from app.models.nodes import Entry, EntryType, Workspace
from app.schemas.resident_assignment import (
    ResidentAssignmentRequest,
    ResidentAssignmentSettings,
)
from app.services import resident_assignment
from app.services.app_graph import (
    catalog_workspace,
    ensure_track_attached_operational_model,
)
from app.services.permissions import get_user_node
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def test_assignment_serializes_revision_and_rolls_back_with_outbox(
    postgres_raw_db, monkeypatch
):
    provider = SimpleNamespace(id="integral_native")
    monkeypatch.setattr(
        "app.services.chat_providers.get_registry",
        lambda: {"integral_native": provider},
    )
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, track, _ = await proposal()
        entry = await Entry.create(track_id=track.id, title="Task")
        await track.connect(entry, edge=CONTAINS)

        async def set_state(state, revision):
            return await resident_assignment.set_resident_assignment(
                user_id=auth.id,
                workspace_id=workspace.id,
                entry_id=entry.id,
                request=ResidentAssignmentRequest(
                    state=state, expected_assignment_revision=revision
                ),
            )

        async def configure(enabled, user_id=auth.id):
            return await resident_assignment.set_resident_assignment_settings(
                user_id=user_id,
                workspace_id=workspace.id,
                settings=ResidentAssignmentSettings(enabled=enabled),
            )

        async def read_state(user_id=auth.id):
            return await resident_assignment.get_resident_assignment(
                user_id=user_id, workspace_id=workspace.id, entry_id=entry.id
            )

        async def edges():
            return await postgres_raw_db.find(
                "edge",
                {"entity": ResidentAssignment._entity_name(), "source": entry.id},
            )

        assert (await read_state()).model_dump() == {
            "state": "withdrawn",
            "revision": 0,
            "can_assign": False,
            "can_withdraw": False,
            "readiness": None,
        }
        with pytest.raises(ResourceNotFoundError, match="Entry not found"):
            await read_state("o.User.foreign")
        other = await Workspace.create(name="Other scope")
        await catalog_workspace(other)
        user = await get_user_node(auth.id)
        await user.connect(other, edge=IS_MEMBER_OF, role="owner")
        with pytest.raises(ResourceNotFoundError, match="Entry not found"):
            await resident_assignment.get_resident_assignment(
                user_id=auth.id, workspace_id=other.id, entry_id=entry.id
            )
        with pytest.raises(InsufficientPermissionsError, match="disabled"):
            await set_state("assigned", 0)
        member_auth, _, _, _ = await proposal()
        member = await get_user_node(member_auth.id)
        await member.connect(workspace, edge=IS_MEMBER_OF, role="member")
        with pytest.raises(InsufficientPermissionsError):
            await configure(True, user_id=member_auth.id)
        assert (await configure(True)).enabled
        assert (await configure(True)).enabled
        assert (
            await resident_assignment.get_resident_assignment_settings(
                user_id=member_auth.id, workspace_id=workspace.id
            )
        ).enabled
        workspace.description = "An ordinary workspace save"
        await workspace.save()
        assert (await read_state()).can_assign

        async def fail_outbox(**kwargs):
            raise RuntimeError("outbox unavailable")

        with monkeypatch.context() as patch:
            patch.setattr(resident_assignment, "insert_revision_event", fail_outbox)
            with pytest.raises(RuntimeError, match="outbox unavailable"):
                await set_state("assigned", 0)
        assert not await edges()

        results = await asyncio.gather(
            set_state("assigned", 0), set_state("assigned", 0), return_exceptions=True
        )
        assert sum(isinstance(result, ResourceConflictError) for result in results) == 1
        assert sorted(
            result.revision for result in results if not isinstance(result, Exception)
        ) == [1]
        assigned = await read_state()
        assert assigned.model_dump() == {
            "state": "assigned",
            "revision": 1,
            "can_assign": False,
            "can_withdraw": True,
            "readiness": "needs_task_setup",
        }
        profile = await ensure_track_attached_operational_model(track)
        task_type = await EntryType.create(
            track_id=track.id,
            name="Task",
            form_schema={
                "fields": [],
                "task_semantics": {"version": 1, "outcome": {"source": "title"}},
            },
        )
        await profile.connect(task_type, edge=CONTAINS)
        entry.type_id = task_type.id
        await entry.save()
        await entry.connect(task_type, edge=IS_OF_TYPE)
        assert (await read_state()).readiness == "awaiting_execution_policy"

        stored_edge = (await edges())[0]
        await postgres_raw_db.find_one_and_update(
            "edge",
            {"id": stored_edge["id"]},
            {"$set": {"context.sponsor_principal_id": "o.User.foreign"}},
        )
        assert (await read_state()).readiness == "needs_reassignment"
        await postgres_raw_db.find_one_and_update(
            "edge",
            {"id": stored_edge["id"]},
            {"$set": {"context.sponsor_principal_id": auth.id}},
        )
        assert (await set_state("assigned", 1)).revision == 1
        assert not (await configure(False)).enabled
        assert (await read_state()).can_withdraw
        assert (await read_state()).readiness == "paused"
        assert (await set_state("withdrawn", 1)).revision == 2
        assert not (await read_state()).can_assign
        assert (await configure(True)).enabled
        assert (await set_state("assigned", 2)).revision == 3
        stored = await edges()
        assert len(stored) == 1 and stored[0]["target"] == workspace.id
        assert stored[0]["context"]["sponsor_principal_id"] == auth.id
        assert stored[0]["context"]["revision"] == 3

        events = await postgres_raw_db.find(
            "object",
            {
                "entity": "OperationEventOutbox",
                "context.event.resource_id": entry.id,
                "context.event.action": RESIDENT_ASSIGNMENT_CHANGED,
            },
        )
        assert sorted(
            row["context"]["event"]["after"]["assignment_revision"] for row in events
        ) == [1, 2, 3]
        assert all(row["context"]["event"]["actor_id"] == auth.id for row in events)
        settings_events = await postgres_raw_db.find(
            "object",
            {
                "entity": "OperationEventOutbox",
                "context.event.resource_id": workspace.id,
                "context.event.action": RESIDENT_ASSIGNMENT_SETTINGS_CHANGED,
            },
        )
        assert len(settings_events) == 3
        assert (
            await resident_assignment.get_resident_assignment_settings(
                user_id=auth.id, workspace_id=workspace.id
            )
        ).enabled
        assert not declaration_matches(
            SimpleNamespace(action="", resource_type="", scope_prefix=""),
            action=RESIDENT_ASSIGNMENT_CHANGED,
            resource_type="Entry",
        )
