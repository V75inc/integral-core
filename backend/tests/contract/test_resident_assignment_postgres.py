"""Resident assignment uses the canonical Entry lock and durable event outbox."""

import asyncio
from types import SimpleNamespace

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive.edges import ResidentAssignment
from app.agentive.services import staging_apply
from app.agentive.services.work_approvals import approve_work_approval
from app.agentive.services.work_budgets import reserve_mandate_budget
from app.agentive.services.work_events import declaration_matches
from app.agentive.services.work_mandates import load_approved_work_mandate
from app.agentive.services.work_worker import process_one_due_item
from app.agentive.staging import StagingError
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
from app.models.nodes import ChatMessage, Entry, EntryType, Workspace
from app.schemas.agentive.work import WorkError
from app.schemas.agentive.work_mandate import WorkMandateRevision
from app.schemas.resident_assignment import (
    ResidentAssignmentRequest,
    ResidentAssignmentSettings,
)
from app.services import (
    resident_assignment,
    resident_assignment_execution,
    resident_assignment_placement,
)
from app.services.app_graph import (
    catalog_workspace,
    ensure_track_attached_operational_model,
)
from app.services.app_operations.event_outbox import deliver_operation_event
from app.services.chat_threads import create_thread
from app.services.permissions import get_user_node
from tests.contract.test_staging_command_apply_postgres import proposal
from tests.contract.test_work_budgets_postgres import request as budget_request
from tests.test_work_mandate_contract import mandate_payload

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def test_assignment_serializes_revision_and_rolls_back_with_outbox(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
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
            "can_request_review": False,
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
            "can_request_review": False,
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

        async def fail_audit(**kwargs):
            raise RuntimeError("audit unavailable")

        async def emit_audit(**kwargs):
            return None

        facts = {
            row["context"]["event"]["after"]["assignment_revision"]: row["context"][
                "outbox_id"
            ]
            for row in events
        }

        async def deliver(revision):
            return await deliver_operation_event(
                outbox_id=facts[revision], database=postgres_raw_db
            )

        async def receipts():
            return await postgres_raw_db.find(
                "object",
                {"entity": "ResidentAssignmentIntake", "context.entry_id": entry.id},
            )

        record_intake = resident_assignment.record_resident_assignment_intake

        async def fail_intake(**kwargs):
            await record_intake(**kwargs)
            raise RuntimeError("intake transaction interrupted")

        with monkeypatch.context() as patch:
            patch.setattr(
                resident_assignment, "record_resident_assignment_intake", fail_intake
            )
            with pytest.raises(RuntimeError, match="intake transaction interrupted"):
                await deliver(3)
        assert await receipts() == []

        monkeypatch.setattr("app.services.change_event.emit_change_event", fail_audit)
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await deliver(3)
        (first,) = await receipts()
        assert first["context"] == {
            "fact_id": facts[3],
            "workspace_id": workspace.id,
            "entry_id": entry.id,
            "assignment_revision": 3,
            "sponsor_principal_id": auth.id,
            "resident_provider_id": provider.id,
            "received_at": first["context"]["received_at"],
        }

        monkeypatch.setattr("app.services.change_event.emit_change_event", emit_audit)
        assert await deliver(3)
        assert not await deliver(3)
        assert await deliver(1)
        assert await deliver(2)
        assert await receipts() == [first]

        def review(revision=3, sponsor=auth.id):
            return resident_assignment.resident_assignment_review_scope(
                workspace_id=workspace.id,
                entry_id=entry.id,
                sponsor=sponsor,
                assignment_revision=revision,
            )

        async with review() as snapshot:
            assert snapshot.intake_fact_id == facts[3]
            assert snapshot.outcome == "Task"
            assert snapshot.definition_id == profile.id
            assert snapshot.definition_revision == profile.version_number
            assert snapshot.track_id == track.id

        entry.title = "Updated outcome"
        entry.record_revision += 1
        await entry.save()
        async with review() as updated:
            assert updated.outcome == "Updated outcome"
            assert updated.entry_revision == snapshot.entry_revision + 1
            assert updated.definition_digest == snapshot.definition_digest
            assert updated.fingerprint() != snapshot.fingerprint()

        thread = await create_thread(
            user_id=auth.id, workspace_id=workspace.id, provider_id=provider.id
        )
        payload = mandate_payload()
        payload.update(
            principal_id=auth.id,
            workspace_id=workspace.id,
            thread_id=thread.id,
            goal=updated.outcome,
            inputs=[{"kind": "entry", "resource_id": entry.id}],
        )
        payload["grants"][0]["resources"] = payload["inputs"]
        revision = WorkMandateRevision.model_validate(payload)

        async def propose_review(fingerprint=updated.fingerprint(), config=revision):
            return await resident_assignment.propose_resident_assignment_review(
                workspace_id=workspace.id,
                entry_id=entry.id,
                sponsor=auth.id,
                assignment_revision=3,
                revision=config,
                expected_task_fingerprint=fingerprint,
            )

        with pytest.raises(ResourceConflictError, match="task has changed"):
            await propose_review(snapshot.fingerprint())
        item, approval = await propose_review()
        again, same = await propose_review()
        assert (item.id, approval.id) == (again.id, same.id)
        changed = revision.model_copy(
            update={
                "limits": revision.limits.model_copy(update={"max_model_requests": 4})
            }
        )
        with pytest.raises(WorkError, match="work.idempotency_conflict"):
            await propose_review(config=changed)
        foreign_thread = await create_thread(
            user_id=member_auth.id, workspace_id=workspace.id, provider_id=provider.id
        )
        with pytest.raises(ResourceNotFoundError, match="preparation thread not found"):
            await propose_review(
                config=revision.model_copy(update={"thread_id": foreign_thread.id})
            )
        stored = await postgres_raw_db.get("object", item.id)
        bound = WorkMandateRevision.model_validate(
            stored["context"]["plan"]["mandate_revision"]
        )
        assert bound.assignment_source.task_fingerprint == updated.fingerprint()
        assert bound.thread_id == thread.id
        assert bound.model_routes == revision.model_routes
        assert bound.limits == revision.limits
        assert item.status == "waiting_for_human"
        assert approval.authority_digest == bound.review_digest()
        with pytest.raises(WorkError, match="work.mandate_admission_required"):
            await approve_work_approval(
                work_approval_id=approval.work_approval_id, decider_id=auth.id
            )

        async def review_view(user_id=auth.id):
            return await resident_assignment.get_resident_assignment_review(
                user_id=user_id, workspace_id=workspace.id, entry_id=entry.id
            )

        visible = await review_view()
        assert (
            visible.can_approve and visible.review_digest == approval.authority_digest
        )
        assert visible.model == revision.model_routes[0].model
        assert "thread_id" not in visible.model_dump()
        assert "credential_ref" not in visible.model_dump()
        try:
            other_view = await review_view(member_auth.id)
        except ResourceNotFoundError:
            other_view = None
        assert other_view is None

        async def decide(reviewer=auth.id, digest=approval.authority_digest):
            return await resident_assignment.approve_resident_assignment_review(
                workspace_id=workspace.id,
                reviewer_id=reviewer,
                work_item_id=item.work_item_id,
                expected_review_digest=digest,
            )

        with pytest.raises(
            InsufficientPermissionsError, match="Workspace review authority"
        ):
            await decide(reviewer=member_auth.id)
        with pytest.raises(ResourceConflictError, match="review has changed"):
            await decide(digest="stale")

        first_decision, replay = await asyncio.gather(decide(), decide())
        assert first_decision.status == "approved"
        assert (await review_view()).approval_status == "approved"
        assert not (await review_view()).can_approve
        assert first_decision.decider_id == auth.id
        assert first_decision.decided_at == replay.decided_at
        stored = await postgres_raw_db.get("object", item.id)
        assert stored["context"]["status"] == "waiting_for_human"
        assert (
            len(
                await postgres_raw_db.find(
                    "object",
                    {
                        "entity": "WorkOutboxEntry",
                        "context.work_item_id": item.work_item_id,
                        "context.topic": "work.resident_assignment_review_approved",
                    },
                )
            )
            == 1
        )
        with pytest.raises(WorkError, match="work.mandate_admission_required"):
            await load_approved_work_mandate(
                work_item_id=item.work_item_id,
                principal_id=auth.id,
                workspace_id=workspace.id,
            )

        async def resolve_route(_revision):
            return SimpleNamespace()

        async def generate(_context, _revision, task):
            assert task.outcome == updated.outcome
            return "Private assignment draft"

        monkeypatch.setattr(
            resident_assignment_execution, "_resolve_assignment_route", resolve_route
        )
        monkeypatch.setattr(
            resident_assignment_execution, "_generate_preparation", generate
        )

        deliver_approvals = (
            resident_assignment_execution.deliver_resident_assignment_approvals
        )
        (pending_event,) = await postgres_raw_db.find(
            "object",
            {
                "entity": "WorkOutboxEntry",
                "context.topic": resident_assignment.ASSIGNMENT_REVIEW_APPROVED,
            },
        )

        async def interrupt_delivery(*args):
            raise RuntimeError("queue transaction interrupted")

        with monkeypatch.context() as patch:
            patch.setattr(
                resident_assignment_execution,
                "_complete_approval_event",
                interrupt_delivery,
            )
            assert await deliver_approvals() == 0
        stored = await postgres_raw_db.get("object", item.id)
        assert stored["context"]["status"] == "waiting_for_human"
        event = await postgres_raw_db.get("object", pending_event["id"])
        assert event["context"]["status"] == "pending"
        assert event["context"]["attempt"] == 1
        await postgres_raw_db.find_one_and_update(
            "object",
            {"id": pending_event["id"]},
            {
                "$set": {
                    "context.available_at": pending_event["context"]["available_at"]
                }
            },
        )
        assert await deliver_approvals() == 1
        assert await deliver_approvals() == 0
        stored = await postgres_raw_db.get("object", item.id)
        event = await postgres_raw_db.get("object", pending_event["id"])
        assert stored["context"]["status"] == "queued"
        assert event["context"]["status"] == "delivered"

        async def reserve():
            return await reserve_mandate_budget(
                work_item_id=item.work_item_id,
                principal_id=auth.id,
                workspace_id=workspace.id,
                thread_id=thread.id,
                request=budget_request(payload),
            )

        with pytest.raises(WorkError, match="work.model_bounds_required"):
            await reserve()
        entry.record_revision += 1
        await entry.save()
        with pytest.raises(WorkError, match="work.assignment_stale"):
            await reserve()
        assert (
            await postgres_raw_db.find(
                "object",
                {
                    "entity": "WorkBudgetReservation",
                    "context.work_item_id": item.work_item_id,
                },
            )
            == []
        )
        with pytest.raises(ResourceConflictError, match="task has changed"):
            await decide()

        entry.record_revision -= 1
        await entry.save()
        completed = await process_one_due_item(work_item_id=item.work_item_id)
        assert completed.status == "succeeded", completed.failure
        message = await ChatMessage.get(completed.result_refs[0])
        assert message.thread_id == thread.id
        assert message.parts == [{"type": "text", "text": "Private assignment draft"}]
        assert await process_one_due_item(work_item_id=item.work_item_id) is None

        placement = await resident_assignment_placement.stage_assignment_placement(
            user_id=auth.id,
            workspace_id=workspace.id,
            entry_id=entry.id,
            review_digest=approval.authority_digest,
        )
        assert not (await Entry.find_one({"id": entry.id})).body
        applied = await staging_apply.bless_and_execute(
            user_id=auth.id, token=placement.token
        )
        assert applied.get("consumed"), applied
        current_entry = await Entry.find_one({"id": entry.id})
        assert (
            current_entry.body
            == "## Resident preparation draft\n\nPrivate assignment draft"
        )
        assert current_entry.record_revision == updated.entry_revision + 1
        with pytest.raises(StagingError, match="already resolved"):
            await staging_apply.bless_and_execute(
                user_id=auth.id, token=placement.token
            )
        assert (
            await Entry.find_one({"id": entry.id})
        ).record_revision == current_entry.record_revision
        # Subsequent cases use this fixture's original reviewed snapshot.
        await entry.save()

        second_item, second_approval = await propose_review(
            config=revision.model_copy(update={"revision": 2})
        )
        await resident_assignment.approve_resident_assignment_review(
            workspace_id=workspace.id,
            reviewer_id=auth.id,
            work_item_id=second_item.work_item_id,
            expected_review_digest=second_approval.authority_digest,
        )
        assert await deliver_approvals() == 0
        (competing,) = await postgres_raw_db.find(
            "object",
            {
                "entity": "WorkOutboxEntry",
                "context.topic": resident_assignment.ASSIGNMENT_REVIEW_APPROVED,
                "context.work_item_id": second_item.work_item_id,
            },
        )
        assert competing["context"]["status"] == "dead_letter"
        assert await process_one_due_item(work_item_id=second_item.work_item_id) is None

        with pytest.raises(ResourceConflictError, match="assignment has changed"):
            async with review(revision=1):
                pytest.fail("a superseded revision entered the review scope")

        assert not (await configure(False)).enabled
        with pytest.raises(InsufficientPermissionsError, match="sponsorship required"):
            async with review():
                pytest.fail("a paused assignment entered the review scope")

        assert (await configure(True)).enabled
        assert (await set_state("withdrawn", 3)).revision == 4
        assert (await set_state("assigned", 4)).revision == 5
        assert (await read_state()).can_request_review
        with pytest.raises(ResourceConflictError, match="intake is pending"):
            await resident_assignment.request_resident_assignment_review(
                user_id=auth.id,
                workspace_id=workspace.id,
                entry_id=entry.id,
                assignment_revision=5,
            )
        (row,) = await postgres_raw_db.find(
            "object",
            {
                "entity": "OperationEventOutbox",
                "context.event.resource_id": entry.id,
                "context.event.action": RESIDENT_ASSIGNMENT_CHANGED,
                "context.event.after.assignment_revision": 5,
            },
        )
        assert await deliver_operation_event(
            outbox_id=row["context"]["outbox_id"], database=postgres_raw_db
        )

        async def review_route(_workspace):
            return revision.model_routes[0]

        monkeypatch.setattr(
            resident_assignment_execution,
            "resolve_assignment_review_route",
            review_route,
        )

        async def request_review():
            await resident_assignment.request_resident_assignment_review(
                user_id=auth.id,
                workspace_id=workspace.id,
                entry_id=entry.id,
                assignment_revision=5,
            )
            return await review_view()

        first, replay = await asyncio.gather(request_review(), request_review())
        assert first.work_item_id == replay.work_item_id
        assert first.approval_status == "pending"
        assert first.max_model_requests == 1
        assert first.max_spend_usd == "0.1"
        assert first.preparation_thread_id is None
        stored = await postgres_raw_db.get("object", f"o.WorkItem.{first.work_item_id}")
        review_config = WorkMandateRevision.model_validate(
            stored["context"]["plan"]["mandate_revision"]
        )
        assert not review_config.grants and review_config.limits.max_tool_calls == 0

        await resident_assignment.approve_resident_assignment_review(
            workspace_id=workspace.id,
            reviewer_id=auth.id,
            work_item_id=first.work_item_id,
            expected_review_digest=first.review_digest,
        )
        assert await deliver_approvals() == 1
        finished = await process_one_due_item(work_item_id=first.work_item_id)
        assert finished.status == "succeeded"
        stale = await resident_assignment_placement.stage_assignment_placement(
            user_id=auth.id,
            workspace_id=workspace.id,
            entry_id=entry.id,
            review_digest=first.review_digest,
        )
        before = await Entry.find_one({"id": entry.id})
        assert (await set_state("withdrawn", 5)).revision == 6
        rejected = await staging_apply.bless_and_execute(
            user_id=auth.id, token=stale.token
        )
        assert not rejected["consumed"]
        assert rejected["execute_result"]["error_code"] == "proposal_changed"
        after = await Entry.find_one({"id": entry.id})
        assert (after.body, after.record_revision) == (
            before.body,
            before.record_revision,
        )
