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


async def _run_observation_worker(work_id, mode, expected_exit, *, kind="watch"):
    import os
    import sys

    from app.config import settings

    worker = """
import asyncio, os, sys
from jvspatial.core.context import GraphContext, set_default_context
from jvspatial.db.factory import create_database
from app.agentive.services import work_recovery, work_worker
from app.services import chat_thread_events

async def main():
    db = create_database(db_type="postgres")
    set_default_context(GraphContext(database=db))
    work_id, mode, kind = sys.argv[1:]
    if kind == "digest":
        from types import SimpleNamespace
        from app.services import chat_providers, resident_opportunities
        chat_providers.get_registry = lambda: {"integral_native": SimpleNamespace(id="integral_native")}
        resident_opportunities._RESULT_LIMIT = 1
    try:
        if mode == "crash":
            async def crash_after_commit(*args, **kwargs):
                os._exit(77)
            chat_thread_events.notify_thread_message = crash_after_commit
            await work_worker.process_one_due_item(worker_id="crashed-watch", work_item_id=work_id)
            raise AssertionError("Observation did not reach notification")
        if mode == "queued":
            result = await work_worker.process_one_due_item(worker_id="fresh-watch", work_item_id=work_id)
            assert result is not None and result.status == "succeeded", result
            return
        from unittest.mock import AsyncMock
        notify = AsyncMock()
        chat_thread_events.notify_thread_message = notify
        report = await work_recovery.run_recovery_pass(reclaim_worker_id="recovered-watch")
        assert report.reclaimed == 1, report
        notify.assert_awaited_once()
        print("__OBSERVATION_MESSAGE__" + notify.await_args.kwargs["message_id"])
    finally:
        await db.close()

asyncio.run(main())
"""
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        worker,
        work_id,
        mode,
        kind,
        env={
            **os.environ,
            "EMBEDDING_STORE_DRIVER": "null",
            "SECRET_KEY": settings.SECRET_KEY,
        },
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(child.communicate(), timeout=60)
    except BaseException:
        if child.returncode is None:
            child.kill()
        await child.wait()
        raise
    assert child.returncode == expected_exit, (stdout.decode(), stderr.decode())
    return stdout.decode()


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

        from app.agentive.tooling.dispatch import dispatch_tool
        from app.services import resident_opportunities

        async def discover(principal_id=auth.id, *, cursor=None):
            result = await dispatch_tool(
                "integral_list_resident_opportunities",
                {"cursor": cursor} if cursor else {},
                principal_id=principal_id,
                scope=workspace.id,
            )
            assert not result.is_error, result
            return result.data

        second_entry = await Entry.create(
            track_id=track.id, type_id=task_type.id, title="Another task"
        )
        await track.connect(second_entry, edge=CONTAINS)
        await second_entry.connect(task_type, edge=IS_OF_TYPE)
        await resident_assignment.set_resident_assignment(
            user_id=auth.id,
            workspace_id=workspace.id,
            entry_id=second_entry.id,
            request=ResidentAssignmentRequest(
                state="assigned", expected_assignment_revision=0
            ),
        )
        discovery = await discover()
        assert discovery == {
            "opportunities": [
                {
                    "entry_id": entry.id,
                    "track_id": track.id,
                    "assignment_revision": 3,
                    "entry_revision": updated.entry_revision,
                    "source_fingerprint": updated.fingerprint(),
                    "due_date": None,
                    "reason": "assigned_awaiting_review",
                    "suggested_action": "request_preparation_review",
                }
            ],
            "scan_complete": True,
            "has_more_eligible": False,
        }
        assert (await discover(member_auth.id))["opportunities"] == []
        second_fact = await postgres_raw_db.find_one(
            "object",
            {
                "entity": "OperationEventOutbox",
                "context.event.resource_id": second_entry.id,
                "context.event.action": RESIDENT_ASSIGNMENT_CHANGED,
            },
        )
        await deliver_operation_event(
            outbox_id=second_fact["context"]["outbox_id"], database=postgres_raw_db
        )
        assert {row["entry_id"] for row in (await discover())["opportunities"]} == {
            entry.id,
            second_entry.id,
        }

        with monkeypatch.context() as page:
            page.setattr(resident_opportunities, "_RESULT_LIMIT", 1)
            first_page = await discover()
            assert first_page["scan_complete"] and first_page["has_more_eligible"]
            second_page = await discover(cursor=first_page["next_cursor"])
            assert "next_cursor" not in second_page
            assert {
                first_page["opportunities"][0]["entry_id"],
                second_page["opportunities"][0]["entry_id"],
            } == {entry.id, second_entry.id}
            mismatched = await dispatch_tool(
                "integral_list_resident_opportunities",
                {"cursor": first_page["next_cursor"]},
                principal_id=member_auth.id,
                scope=workspace.id,
            )
            assert mismatched.is_error

            page.setattr(resident_opportunities, "_SCAN_LIMIT", 1)
            first_scan = await discover()
            assert not first_scan["scan_complete"]
            second_scan = await discover(cursor=first_scan["next_cursor"])
            assert second_scan["scan_complete"] and "next_cursor" not in second_scan
            assert {
                first_scan["opportunities"][0]["entry_id"],
                second_scan["opportunities"][0]["entry_id"],
            } == {entry.id, second_entry.id}

        from app.agentive.nodes import RoutineTask
        from app.agentive.staging import create_staged_change
        from app.services import routine_task_scheduler
        from app.services.agent_scope import current_scope_workspace_id
        from app.services.chat_threads import list_messages
        from app.utils.time import utc_now_iso

        digest_thread = await create_thread(
            user_id=auth.id, workspace_id=workspace.id, provider_id=provider.id
        )
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            staged_digest = await create_staged_change(
                user_id=auth.id,
                session_id=digest_thread.id,
                kind="routine_task_create",
                summary="Assignment digest",
                diff_human="Read-only assignment digest",
                diff_machine={},
                payload={
                    "workspace_id": workspace.id,
                    "thread_id": digest_thread.id,
                    "instruction": "Find assignments ready for review",
                    "delivery_mode": "assignment_digest",
                    "cron": "0 9 * * *",
                    "timezone": "UTC",
                },
            )
        finally:
            current_scope_workspace_id.reset(reset)
        approved = await staging_apply.bless_and_execute(
            user_id=auth.id, token=staged_digest.token
        )
        assert approved.get("consumed"), approved
        digest_routine = await RoutineTask.find_one({"thread_id": digest_thread.id})

        async def enqueue_digest():
            await postgres_raw_db.find_one_and_update(
                "node",
                {"id": digest_routine.id},
                {"$set": {"context.next_run_at": utc_now_iso()}},
            )
            assert await routine_task_scheduler.run_scheduler_pass() == 1
            row = await postgres_raw_db.get("node", digest_routine.id)
            return row["context"]["watch_pending_work_id"]

        async def fire_digest():
            work_id = await enqueue_digest()
            result = await process_one_due_item(
                worker_id="assignment-digest-test",
                work_item_id=work_id.removeprefix("o.WorkItem."),
            )
            assert result is not None and result.status == "succeeded", result
            assert await routine_task_scheduler.run_scheduler_pass() == 0

        with monkeypatch.context() as page:
            page.setattr(resident_opportunities, "_RESULT_LIMIT", 1)
            from datetime import datetime, timedelta, timezone

            work_id = await enqueue_digest()
            await _run_observation_worker(
                work_id.removeprefix("o.WorkItem."), "crash", 77, kind="digest"
            )
            row = await postgres_raw_db.get("node", digest_routine.id)
            checkpoint = row["context"]["opportunity_cursor"]
            assert checkpoint
            (first_digest,) = await list_messages(digest_thread)
            stored = await postgres_raw_db.get("object", work_id)
            assert stored["context"]["status"] == "running"
            await postgres_raw_db.find_one_and_update(
                "object",
                {"id": work_id, "context.status": "running"},
                {
                    "$set": {
                        "context.lease_expires_at": (
                            datetime.now(timezone.utc) - timedelta(seconds=1)
                        ).isoformat()
                    }
                },
            )
            recovered = await _run_observation_worker(
                work_id.removeprefix("o.WorkItem."), "recover", 0, kind="digest"
            )
            assert f"__OBSERVATION_MESSAGE__{first_digest.id}" in recovered.splitlines()
            stored = await postgres_raw_db.get("object", work_id)
            assert stored["context"]["status"] == "succeeded"
            row = await postgres_raw_db.get("node", digest_routine.id)
            assert row["context"]["opportunity_cursor"] == checkpoint
            assert len(await list_messages(digest_thread)) == 1
            assert await routine_task_scheduler.run_scheduler_pass() == 0
            await fire_digest()
            row = await postgres_raw_db.get("node", digest_routine.id)
            assert row["context"]["opportunity_cursor"] is None
            await fire_digest()
            await fire_digest()
            assert len(await list_messages(digest_thread)) == 2

        second_entry.body = "An unrelated body edit"
        await second_entry.save()
        await fire_digest()
        assert len(await list_messages(digest_thread)) == 2
        second_entry.title = "Changed task outcome"
        await second_entry.save()
        await fire_digest()
        alerts = await list_messages(digest_thread)
        assert len(alerts) == 3
        assert second_entry.id in alerts[-1].parts[0]["text"]
        assert entry.id not in alerts[-1].parts[0]["text"]

        from app.agentive.api.routines import set_routine_opportunity_disposition
        from app.agentive.tooling.invoke import invoke_route_in_process

        async def disposition(message, principal=auth.id, *, action="dismiss"):
            return await invoke_route_in_process(
                set_routine_opportunity_disposition,
                principal_id=principal,
                scope=workspace.id,
                message_id=message.id,
                entry_id=second_entry.id,
                action=action,
            )

        prior = next(
            msg for msg in alerts[:-1] if second_entry.id in msg.parts[0]["text"]
        )
        with pytest.raises(ResourceConflictError, match="superseded"):
            await disposition(prior)
        with pytest.raises(ResourceNotFoundError, match="Suggestion not found"):
            await disposition(alerts[-1], member_auth.id)
        assert await disposition(alerts[-1]) == {"dismissed": True}
        assert await disposition(alerts[-1]) == {"dismissed": True}
        stored_message = await postgres_raw_db.get("node", alerts[-1].id)
        assert stored_message["context"]["provider_metadata"]["assignment_suggestions"][
            0
        ]["dismissed"]

        await fire_digest()
        assert len(await list_messages(digest_thread)) == 3
        second_entry.title = "New outcome after dismissal"
        await second_entry.save()
        await fire_digest()
        alerts = await list_messages(digest_thread)
        assert len(alerts) == 4
        assert (
            not alerts[-1]
            .provider_metadata["assignment_suggestions"][0]
            .get("dismissed")
        )

        from app.agentive.services import routine_assignment_digest
        from app.services import resident_opportunity_actions

        now = datetime.now(timezone.utc)
        follow_up = now + timedelta(hours=24)
        with monkeypatch.context() as clock:
            clock.setattr(resident_opportunity_actions, "utc_now", lambda: now)
            saved = await disposition(alerts[-1], action="later")
            assert saved == {"deferred_until": follow_up.isoformat()}
            assert await disposition(alerts[-1], action="later") == saved
            old_message = await postgres_raw_db.get("node", alerts[-1].id)
            assert (
                old_message["context"]["provider_metadata"]["assignment_suggestions"][
                    0
                ]["deferred_until"]
                == follow_up.isoformat()
            )

            clock.setattr(
                routine_assignment_digest,
                "utc_now",
                lambda: follow_up - timedelta(seconds=1),
            )
            await fire_digest()
            assert len(await list_messages(digest_thread)) == 4
            clock.setattr(routine_assignment_digest, "utc_now", lambda: follow_up)
            await fire_digest()
            await fire_digest()
            alerts = await list_messages(digest_thread)
            assert len(alerts) == 5
            assert second_entry.id in alerts[-1].parts[0]["text"]
            assert (
                not alerts[-1]
                .provider_metadata["assignment_suggestions"][0]
                .get("deferred_until")
            )

        # A scheduler tick arriving after the next cron minute can account
        # for the completed run and durably admit one successor immediately.
        await postgres_raw_db.find_one_and_update(
            "node", {"id": digest_routine.id}, {"$set": {"context.cron": "* * * * *"}}
        )
        completed_id = await enqueue_digest()
        completed = await process_one_due_item(
            worker_id="digest-cadence",
            work_item_id=completed_id.removeprefix("o.WorkItem."),
        )
        assert completed is not None and completed.status == "succeeded"
        from app.agentive.services.routine_tasks import compute_next_run_at

        next_due = compute_next_run_at(
            "* * * * *", "UTC", base_iso=completed.updated_at
        )
        delayed_tick = (
            datetime.fromisoformat(next_due) + timedelta(minutes=2)
        ).isoformat()
        with monkeypatch.context() as clock:
            clock.setattr(routine_task_scheduler, "utc_now_iso", lambda: delayed_tick)
            assert await routine_task_scheduler.run_scheduler_pass() == 1
            assert await routine_task_scheduler.run_scheduler_pass() == 0
        row = await postgres_raw_db.get("node", digest_routine.id)
        assert row["context"]["next_run_at"] == next_due
        assert row["context"]["last_run_at"] == completed.updated_at
        successor_id = row["context"]["watch_pending_work_id"]
        assert successor_id and successor_id != completed_id
        successor = await postgres_raw_db.get("object", successor_id)
        assert successor["context"]["status"] == "queued"
        assert successor["context"]["input_payload"]["scheduled_for"] == next_due

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

        async def suggestion_review_route(_workspace):
            return revision.model_routes[0]

        with monkeypatch.context() as route:
            route.setattr(
                resident_assignment_execution,
                "resolve_assignment_review_route",
                suggestion_review_route,
            )
            latest = alerts[-1]
            title = second_entry.title
            second_entry.title = "Changed before Prepare"
            await second_entry.save()
            with pytest.raises(ResourceConflictError, match="task changed"):
                await disposition(latest, action="prepare")
            second_entry.title = title
            await second_entry.save()
            with pytest.raises(ResourceNotFoundError, match="Suggestion not found"):
                await disposition(latest, member_auth.id, action="prepare")
            assert await disposition(latest, action="accept") == {"accepted": True}
            assert await disposition(latest, action="accept") == {"accepted": True}
            assert (
                await resident_assignment.get_resident_assignment_review(
                    user_id=auth.id, workspace_id=workspace.id, entry_id=second_entry.id
                )
                is None
            )
            accepted = (await discover())["opportunities"][0]
            assert accepted["entry_id"] == second_entry.id and accepted["accepted"]
            stored_message = await postgres_raw_db.get("node", latest.id)
            assert stored_message["context"]["provider_metadata"][
                "assignment_suggestions"
            ][0]["accepted"]

            assert await disposition(latest, action="prepare") == {
                "review_requested": True
            }
            assert await disposition(latest, action="prepare") == {
                "review_requested": True
            }
            review_view_from_chat = (
                await resident_assignment.get_resident_assignment_review(
                    user_id=auth.id, workspace_id=workspace.id, entry_id=second_entry.id
                )
            )
            assert review_view_from_chat.approval_status == "pending"
            assert review_view_from_chat.status == "waiting_for_human"
            assert review_view_from_chat.max_model_requests == 1
            assert review_view_from_chat.preparation_thread_id is None
            stored_message = await postgres_raw_db.get("node", latest.id)
            assert stored_message["context"]["provider_metadata"][
                "assignment_suggestions"
            ][0]["review_requested"]

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
        assert (await discover())["opportunities"] == []
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


async def test_reviewed_task_watch_only_posts_material_changes(
    postgres_raw_db, monkeypatch
):
    from datetime import datetime, timedelta, timezone

    from app.agentive.api.routines import patch_routine
    from app.agentive.nodes import RoutineTask
    from app.agentive.services import routine_task_watch
    from app.agentive.services.routine_tasks import enqueue_routine_turn_work
    from app.agentive.staging import create_staged_change
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.services import routine_task_scheduler
    from app.services.agent_scope import current_scope_workspace_id
    from app.services.chat_threads import list_messages

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, track, _ = await proposal()
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
        entry = await Entry.create(
            track_id=track.id, title="First outcome", type_id=task_type.id
        )
        await track.connect(entry, edge=CONTAINS)
        await entry.connect(task_type, edge=IS_OF_TYPE)
        thread = await create_thread(
            user_id=auth.id, workspace_id=workspace.id, provider_id="integral_native"
        )
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            staged = await create_staged_change(
                user_id=auth.id,
                session_id=None,
                kind="routine_task_create",
                summary="Watch this task",
                diff_human="Read-only task watch",
                diff_machine={},
                payload={
                    "workspace_id": workspace.id,
                    "thread_id": thread.id,
                    "instruction": "Watch this task",
                    "cron": "0 9 * * *",
                    "timezone": "UTC",
                    "delivery_mode": "task_watch",
                    "watch_entry_id": entry.id,
                    "max_runs": 2,
                },
            )
        finally:
            current_scope_workspace_id.reset(reset)
        assert await RoutineTask.find({"user_id": auth.id}) == []
        result = await staging_apply.bless_and_execute(
            user_id=auth.id, token=staged.token
        )
        assert result.get("consumed"), result
        (routine,) = await RoutineTask.find({"user_id": auth.id})

        async def enqueue(slot):
            return await enqueue_routine_turn_work(
                routine_id=routine.id,
                principal_id=auth.id,
                workspace_id=workspace.id,
                scheduled_for=f"2026-11-{slot:02d}T09:00:00+00:00",
                thread_id=thread.id,
            )

        async def fire(slot, expected_status="succeeded"):
            work = await enqueue(slot)
            result = await process_one_due_item(
                worker_id="task-watch-test", work_item_id=work.work_item_id
            )
            assert result is not None
            assert result.status == expected_status, result.failure

        await fire(1)
        entry.body = "An unrelated body edit"
        await entry.save()
        await fire(2)
        assert await list_messages(thread) == []
        entry.title = "Changed outcome"
        await entry.save()
        work = await enqueue(3)

        async def run_worker(mode, expected_exit, *, work_id=work.work_item_id):
            return await _run_observation_worker(work_id, mode, expected_exit)

        await run_worker("crash", 77)
        stored = await postgres_raw_db.get("object", work.id)
        assert stored["context"]["status"] == "running"
        assert len(await list_messages(thread)) == 1
        entry.title = "Second changed outcome"
        await entry.save()
        await postgres_raw_db.find_one_and_update(
            "object",
            {"id": work.id, "context.status": "running"},
            {
                "$set": {
                    "context.lease_expires_at": (
                        datetime.now(timezone.utc) - timedelta(seconds=1)
                    ).isoformat()
                }
            },
        )
        recovered = await run_worker("recover", 0)
        stored = await postgres_raw_db.get("object", work.id)
        assert stored["context"]["status"] == "succeeded"
        messages = await list_messages(thread)
        assert len(messages) == 1
        assert f"__OBSERVATION_MESSAGE__{messages[0].id}" in recovered.splitlines()
        assert f"/tracks/{track.id}?entry={entry.id}" in messages[0].parts[0]["text"]
        assert "Changed outcome" not in messages[0].parts[0]["text"]
        due_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        await postgres_raw_db.find_one_and_update(
            "node", {"id": routine.id}, {"$set": {"context.next_run_at": due_at}}
        )
        work_query = {
            "entity": "WorkItem",
            "context.input_payload.routine_id": routine.id,
            "context.input_payload.scheduled_for": due_at,
        }
        save = RoutineTask.save

        async def interrupt_handoff(task, *args, **kwargs):
            if task.id == routine.id and task.watch_pending_work_id:
                raise RuntimeError("injected task-watch handoff interruption")
            return await save(task, *args, **kwargs)

        with monkeypatch.context() as fault:
            fault.setattr(RoutineTask, "save", interrupt_handoff)
            assert await routine_task_scheduler.run_scheduler_pass() == 0
        assert await postgres_raw_db.find("object", work_query) == []
        stored_routine = await postgres_raw_db.get("node", routine.id)
        assert stored_routine["context"]["next_run_at"] == due_at

        passes = await asyncio.gather(
            routine_task_scheduler.run_scheduler_pass(),
            routine_task_scheduler.run_scheduler_pass(),
        )
        assert sorted(passes) == [0, 1]
        rows = await postgres_raw_db.find("object", work_query)
        (queued,) = rows
        assert queued["context"]["status"] == "queued"
        stored_routine = await postgres_raw_db.get("node", routine.id)
        assert stored_routine["context"]["next_run_at"] == due_at
        assert stored_routine["context"]["watch_pending_work_id"] == queued["id"]
        assert len(await list_messages(thread)) == 1

        await run_worker("queued", 0, work_id=queued["context"]["work_item_id"])
        assert await routine_task_scheduler.run_scheduler_pass() == 0
        assert await routine_task_scheduler.run_scheduler_pass() == 0
        stored_routine = await postgres_raw_db.get("node", routine.id)
        assert stored_routine["context"]["last_run_status"] == "success"
        assert stored_routine["context"]["run_count"] == 1
        assert not stored_routine["context"]["watch_pending_work_id"]
        assert stored_routine["context"]["next_run_at"] > due_at
        await fire(5)
        assert len(await list_messages(thread)) == 2
        entry.type_id = ""
        await entry.save()
        await fire(6, "failed")
        assert len(await list_messages(thread)) == 2

        entry.type_id = task_type.id
        entry.title = "Changed during snooze"
        await entry.save()
        stale_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        await postgres_raw_db.find_one_and_update(
            "node", {"id": routine.id}, {"$set": {"context.next_run_at": stale_at}}
        )
        assert await routine_task_scheduler.run_scheduler_pass() == 1
        (stale,) = await postgres_raw_db.find(
            "object",
            {**work_query, "context.input_payload.scheduled_for": stale_at},
        )
        wake = (datetime.now(timezone.utc) + timedelta(days=2)).replace(
            hour=22, minute=30, second=0, microsecond=0
        )
        wake_at = wake.isoformat()
        delivery_at = (wake + timedelta(hours=8, minutes=30)).isoformat()

        async def patch_watch(**body):
            return await invoke_route_in_process(
                patch_routine,
                principal_id=auth.id,
                scope=workspace.id,
                routine_id=routine.id,
                json_body=body,
            )

        snoozed = await patch_watch(snooze_until=wake_at)
        assert snoozed["snoozed_until"] == snoozed["next_run_at"] == wake_at
        assert await routine_task_scheduler.run_scheduler_pass() == 0
        stale_result = await process_one_due_item(
            worker_id="stale-watch", work_item_id=stale["context"]["work_item_id"]
        )
        assert stale_result is not None and stale_result.status == "cancelled"
        assert len(await list_messages(thread)) == 2

        resumed = await patch_watch(clear_snooze=True)
        assert resumed["snoozed_until"] is None
        assert datetime.fromisoformat(resumed["next_run_at"]) <= datetime.now(
            timezone.utc
        )
        quiet_hours = {"start": "22:00", "end": "07:00"}
        patched = await patch_watch(snooze_until=wake_at, quiet_hours=quiet_hours)
        assert patched["quiet_hours"] == quiet_hours
        monkeypatch.setattr(routine_task_scheduler, "utc_now_iso", lambda: wake_at)
        monkeypatch.setattr(routine_task_watch, "utc_now", lambda: wake)
        assert await routine_task_scheduler.run_scheduler_pass() == 1
        (quiet_work,) = await postgres_raw_db.find(
            "object", {**work_query, "context.input_payload.scheduled_for": wake_at}
        )
        result = await process_one_due_item(
            worker_id="quiet-watch", work_item_id=quiet_work["context"]["work_item_id"]
        )
        assert result is not None and result.status == "succeeded", result
        assert await routine_task_scheduler.run_scheduler_pass() == 0
        routine_row = await postgres_raw_db.get("node", routine.id)
        assert routine_row["context"]["snoozed_until"] is None
        assert routine_row["context"]["watch_delivery_after"] == delivery_at
        assert routine_row["context"]["next_run_at"] == delivery_at
        assert routine_row["context"]["run_count"] == 1
        assert len(await list_messages(thread)) == 2

        entry.title = "Changed again during quiet hours"
        await entry.save()
        monkeypatch.setattr(routine_task_scheduler, "utc_now_iso", lambda: delivery_at)
        monkeypatch.setattr(
            routine_task_watch, "utc_now", lambda: datetime.fromisoformat(delivery_at)
        )
        assert await routine_task_scheduler.run_scheduler_pass() == 1
        (allowed_work,) = await postgres_raw_db.find(
            "object", {**work_query, "context.input_payload.scheduled_for": delivery_at}
        )
        result = await process_one_due_item(
            worker_id="delivery-watch",
            work_item_id=allowed_work["context"]["work_item_id"],
        )
        assert result is not None and result.status == "succeeded", result
        assert await routine_task_scheduler.run_scheduler_pass() == 0
        routine_row = await postgres_raw_db.get("node", routine.id)
        assert routine_row["context"]["watch_delivery_after"] is None
        assert routine_row["context"]["status"] == "completed"
        assert routine_row["context"]["run_count"] == 2
        assert len(await list_messages(thread)) == 3
