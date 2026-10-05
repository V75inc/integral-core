"""Transactional, tenant-checked lifecycle for native Harness sessions."""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.pydantic_ai_compat import to_jsonable_python
from app.api.errors import InsufficientPermissionsError, ResourceConflictError
from app.models.edges import HAS_HARNESS_SESSION
from app.models.harness_records import (
    HarnessCheckpointManifestRecord,
    HarnessEventRecord,
    HarnessModelRequestRecord,
    HarnessPlanState,
    HarnessRunRecord,
    HarnessSnapshotRecord,
    HarnessToolEffectRecord,
)
from app.models.nodes import ChatThread, HarnessSession
from app.schemas.agentive.work import WorkExecutionContext
from app.services.app_operations.transaction_scope import (
    OperationTransactionUnavailable,
    graph_transaction_available,
    postgres_graph_transaction,
)

HarnessSessionExport = dict[str, Any]


@asynccontextmanager
async def _harness_session_transaction(
    work_execution_context: WorkExecutionContext | None,
):
    """Reuse the authority transaction for durable-worker session writes."""
    if work_execution_context is not None:
        from app.agentive.services.work_items import authorized_work_item_effect

        async with authorized_work_item_effect(work_execution_context) as graph:
            yield graph.database
        return
    async with postgres_graph_transaction() as transaction:
        yield transaction


def _export_value(value: Any) -> Any:
    """Convert framework dataclasses and Pydantic contracts to JSON values."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return to_jsonable_python(asdict(value))
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_updated_at(previous: str | None) -> str:
    """Return a valid ISO timestamp guaranteed to differ from the CAS value."""
    value = datetime.now(timezone.utc)
    if previous:
        prior = datetime.fromisoformat(previous.replace("Z", "+00:00"))
        if value == prior:
            value = prior + timedelta(microseconds=1)
    return value.isoformat()


async def _find_session_by_id(session_id: str) -> HarnessSession | None:
    """Look up a session through transaction-compatible jvspatial ``find``."""
    matches = await HarnessSession.find({"session_id": session_id})
    if len(matches) > 1:
        raise ResourceConflictError(
            message="Harness session ID is not unique",
            details={"reason": "harness_session_id_collision"},
        )
    return matches[0] if matches else None


async def ensure_harness_session(
    *,
    scope: HarnessExecutionScope,
    binding_id: str,
    work_execution_context: WorkExecutionContext | None = None,
) -> HarnessSession:
    """Return the active session or atomically activate a new binding session.

    The authenticated execution scope is trusted input. This service verifies
    it against the private ChatThread, cross-checks the active pointer against
    the typed graph edge, and changes the generation and edge in one jvspatial
    transaction. PostgreSQL's conditional update is the cross-process CAS;
    stores without public transaction/CAS support fail closed.
    """
    binding_id = binding_id.strip()
    if not binding_id:
        raise ValueError("Harness binding ID is required")
    if not graph_transaction_available():
        raise OperationTransactionUnavailable(
            "Harness session activation requires a transactional shared store"
        )

    async with _harness_session_transaction(work_execution_context) as transaction:
        thread = await ChatThread.get(scope.thread_id)
        if thread is None:
            raise InsufficientPermissionsError(message="Thread unavailable")
        if (
            thread.id != scope.thread_id
            or thread.user_id != scope.principal_id
            or thread.workspace_id != scope.workspace_id
            or scope.tenant_id != thread.workspace_id
        ):
            raise InsufficientPermissionsError(message="Thread scope mismatch")

        context = await thread.get_context()
        active_children = await HarnessSession.find(
            {"thread_id": thread.id, "status": "active"}
        )
        active_id = thread.active_harness_session_id
        current = await HarnessSession.get(active_id) if active_id else None
        if active_id:
            linked = await context.find_edges_between(
                thread.id, active_id, edge_class=HAS_HARNESS_SESSION
            )
            if (
                current is None
                or not linked
                or len(active_children) != 1
                or active_children[0].id != active_id
                or current.thread_id != thread.id
                or current.workspace_id != thread.workspace_id
                or current.principal_id != thread.user_id
                or current.status != "active"
            ):
                raise ResourceConflictError(
                    message="Active session pointer failed graph validation",
                    details={"reason": "harness_session_pointer_mismatch"},
                )
        elif active_children:
            raise ResourceConflictError(
                message="Active Harness session has no thread pointer",
                details={"reason": "harness_session_pointer_missing"},
            )

        if current is not None and current.binding_id == binding_id:
            if (
                current.principal_id != scope.principal_id
                or current.workspace_id != scope.workspace_id
            ):
                raise ResourceConflictError(
                    message="Active Harness session scope is inconsistent",
                    details={"reason": "harness_session_scope_mismatch"},
                )
            if current.session_id == scope.session_id:
                if (
                    current.permission_revision == scope.permission_revision
                    and current.capability_version == scope.capability_version
                ):
                    return current
                raise ResourceConflictError(
                    message=(
                        "Permission or capability revision changed; a new "
                        "session ID is required"
                    ),
                    details={"reason": "harness_session_revision_changed"},
                )

        collision = await _find_session_by_id(scope.session_id)
        if collision is not None:
            raise ResourceConflictError(
                message="Harness session ID is already in use",
                details={"reason": "harness_session_id_collision"},
            )

        if current is not None:
            current.status = "suspended"
            current.updated_at = _now()
            await current.save()

        session = HarnessSession(
            session_id=scope.session_id,
            thread_id=thread.id,
            workspace_id=scope.workspace_id,
            principal_id=scope.principal_id,
            binding_id=binding_id,
            binding_generation=int(thread.harness_session_generation or 0) + 1,
            status="active",
            permission_revision=scope.permission_revision,
            capability_version=scope.capability_version,
            created_at=_now(),
            updated_at=_now(),
        )
        updated_at = _next_updated_at(thread.updated_at)
        generation = session.binding_generation
        query: dict[str, Any] = {
            "id": thread.id,
            # updated_at predates the Harness fields and exists on legacy
            # ChatThreads, making it a migration-free optimistic CAS token.
            "context.updated_at": thread.updated_at,
        }
        updated = await transaction.find_one_and_update(
            "node",
            query,
            {
                "$set": {
                    "context.active_harness_session_id": session.id,
                    "context.harness_session_generation": generation,
                    "context.updated_at": updated_at,
                }
            },
        )
        if updated is None:
            raise ResourceConflictError(
                message="Harness session activation lost a generation race",
                details={"reason": "harness_session_generation_conflict"},
            )

        # Node and typed structural edge share the transaction above.
        await session.save()
        await thread.connect(
            session,
            edge=HAS_HARNESS_SESSION,
            binding_id=binding_id,
            generation=session.binding_generation,
            bound_at=session.created_at,
        )
        return session


async def claim_harness_run(
    *,
    scope: HarnessExecutionScope,
    work_execution_context: WorkExecutionContext | None = None,
) -> HarnessSession:
    """Persist the active run fence in a short PostgreSQL graph transaction."""
    if not graph_transaction_available():
        raise OperationTransactionUnavailable(
            "Harness run claims require a transactional shared store"
        )

    async with _harness_session_transaction(work_execution_context):
        session = await _find_session_by_id(scope.session_id)
        if session is None:
            raise InsufficientPermissionsError(message="Harness session unavailable")
        if (
            session.thread_id != scope.thread_id
            or session.principal_id != scope.principal_id
            or session.workspace_id != scope.workspace_id
            or session.status != "active"
            or session.binding_generation < 1
        ):
            raise InsufficientPermissionsError(message="Harness session scope mismatch")

        session.last_run_id = scope.run_id
        session.updated_at = _now()
        await session.save()
        return session


async def transition_harness_session(
    *,
    scope: HarnessExecutionScope,
    status: str,
) -> HarnessSession:
    """Suspend or terminalize one scoped session and clear its active pointer.

    Sessions remain attached for audit and continuity history. This operation
    never deletes transcript data; retention and explicit hard-delete are
    separate owner-authorized lifecycle operations.
    """
    if status not in {"suspended", "closed", "expired", "revoked"}:
        raise ValueError("unsupported Harness session transition")
    if not graph_transaction_available():
        raise OperationTransactionUnavailable(
            "Harness session lifecycle requires a transactional shared store"
        )

    async with postgres_graph_transaction() as transaction:
        session = await _find_session_by_id(scope.session_id)
        if session is None:
            raise InsufficientPermissionsError(message="Session unavailable")
        thread = await ChatThread.get(scope.thread_id)
        if thread is None:
            raise InsufficientPermissionsError(message="Thread unavailable")
        if (
            session.thread_id != thread.id
            or session.principal_id != scope.principal_id
            or session.workspace_id != scope.workspace_id
            or thread.user_id != scope.principal_id
            or thread.workspace_id != scope.workspace_id
        ):
            raise InsufficientPermissionsError(message="Invalid session scope")
        context = await thread.get_context()
        links = await context.find_edges_between(
            thread.id, session.id, edge_class=HAS_HARNESS_SESSION
        )
        if not links:
            raise ResourceConflictError(
                message="Harness session is detached from its ChatThread",
                details={"reason": "harness_session_edge_missing"},
            )
        if session.status in {"closed", "expired", "revoked"}:
            if session.status == status:
                return session
            raise ResourceConflictError(
                message="A terminal Harness session cannot be reopened",
                details={"reason": "harness_session_terminal"},
            )
        if session.status == status:
            return session

        if session.status == "active":
            if thread.active_harness_session_id != session.id:
                raise ResourceConflictError(
                    message="Active session pointer failed graph validation",
                    details={"reason": "harness_session_pointer_mismatch"},
                )
            updated_at = _next_updated_at(thread.updated_at)
            updated = await transaction.find_one_and_update(
                "node",
                {"id": thread.id, "context.updated_at": thread.updated_at},
                {
                    "$set": {
                        "context.active_harness_session_id": None,
                        "context.updated_at": updated_at,
                    }
                },
            )
            if updated is None:
                raise ResourceConflictError(
                    message="Harness session transition lost a thread race",
                    details={"reason": "harness_session_generation_conflict"},
                )

        session.status = status
        session.updated_at = _now()
        await session.save()
        return session


async def advance_harness_checkpoint_pointer(
    *, scope: HarnessExecutionScope, snapshot_id: str
) -> HarnessSession:
    """Advance the safe checkpoint pointer if this run still owns the session.

    The durable session's ``last_run_id`` is the chat invocation fence. A newer
    invocation replaces it before doing model/tool work, so a stale streaming
    task cannot move the pointer after losing ownership. The manifest is
    authenticated and checked before the atomic database update.
    """
    from jvspatial.core.context import get_default_context

    from app.agentive.harness.checkpoint_manifests import (
        load_checkpoint_manifest,
        manifest_record_id,
    )

    manifest = await load_checkpoint_manifest(scope=scope, snapshot_id=snapshot_id)
    if manifest is None or not manifest.safe_for_resume:
        raise ResourceConflictError(
            message="Checkpoint manifest is absent or not safe to resume",
            details={"reason": "harness_checkpoint_manifest_unavailable"},
        )

    sessions = await HarnessSession.find({"session_id": scope.session_id})
    if len(sessions) != 1:
        raise ResourceConflictError(
            message="Harness session is unavailable or ambiguous",
            details={"reason": "harness_session_pointer_mismatch"},
        )
    session = sessions[0]
    if (
        session.thread_id != scope.thread_id
        or session.workspace_id != scope.workspace_id
        or session.principal_id != scope.principal_id
        or session.status != "active"
        or session.binding_generation < 1
    ):
        raise InsufficientPermissionsError(message="Harness session scope mismatch")

    checkpoint_record_id = manifest_record_id(scope=scope, snapshot_id=snapshot_id)
    updated_at = _now()
    context = get_default_context()
    updated = await context.database.find_one_and_update(
        "node",
        {
            "id": session.id,
            "context.session_id": scope.session_id,
            "context.thread_id": scope.thread_id,
            "context.workspace_id": scope.workspace_id,
            "context.principal_id": scope.principal_id,
            "context.status": "active",
            "context.last_run_id": scope.run_id,
        },
        {
            "$set": {
                "context.last_checkpoint_id": checkpoint_record_id,
                "context.last_checkpoint_run_id": scope.run_id,
                "context.updated_at": updated_at,
            }
        },
    )
    if updated is None:
        raise ResourceConflictError(
            message="Harness checkpoint pointer lost the active run fence",
            details={"reason": "harness_checkpoint_fence_lost"},
        )
    session.last_checkpoint_id = checkpoint_record_id
    session.last_checkpoint_run_id = scope.run_id
    session.updated_at = updated_at
    return session


async def export_harness_session(
    *, scope: HarnessExecutionScope
) -> HarnessSessionExport:
    """Export an owner's authorized session and decrypted run history."""
    session = await _find_session_by_id(scope.session_id)
    thread = await ChatThread.get(scope.thread_id)
    if session is None or thread is None:
        raise InsufficientPermissionsError(message="Session unavailable")
    if (
        session.thread_id != thread.id
        or session.principal_id != scope.principal_id
        or session.workspace_id != scope.workspace_id
        or thread.user_id != scope.principal_id
        or thread.workspace_id != scope.workspace_id
    ):
        raise InsufficientPermissionsError(message="Session scope mismatch")
    context = await thread.get_context()
    edges = await context.find_edges_between(
        thread.id, session.id, edge_class=HAS_HARNESS_SESSION
    )
    if not edges:
        raise ResourceConflictError(
            message="Harness session is detached from its ChatThread",
            details={"reason": "harness_session_edge_missing"},
        )

    from app.agentive.harness.checkpoint_manifests import (
        load_checkpoint_manifest,
    )
    from app.agentive.harness.jvspatial_store import JvSpatialStepStore
    from app.agentive.harness.model_observations import (
        list_model_request_observations,
    )
    from app.agentive.harness.plan_store import JvSpatialPlanStore
    from app.agentive.harness.scoped_store import ScopedStepStore

    store = ScopedStepStore(store=JvSpatialStepStore(scope=scope), scope=scope)
    runs = await store.list_runs(conversation_id=scope.session_id)
    exported_runs: list[dict[str, Any]] = []
    for run in runs:
        run_scope = scope.model_copy(update={"run_id": run.run_id})
        snapshots = await store.list_snapshots(
            run_id=run.run_id, include_interrupted=True
        )
        effects = await store.list_tool_effects(run_id=run.run_id)
        exported_effects = []
        for effect in effects:
            exported_effects.append(_export_value(effect))
        exported_snapshots = []
        exported_manifests = []
        for snapshot in snapshots:
            exported_snapshots.append(_export_value(snapshot))
            if snapshot.idempotency_key:
                manifest = await load_checkpoint_manifest(
                    scope=run_scope, snapshot_id=snapshot.idempotency_key
                )
                if manifest is not None:
                    exported_manifests.append(_export_value(manifest))
        exported_runs.append(
            {
                "run": _export_value(run),
                "events": [
                    _export_value(event)
                    for event in await store.list_events(run_id=run.run_id)
                ],
                "snapshots": exported_snapshots,
                "checkpoint_manifests": exported_manifests,
                "tool_effects": exported_effects,
                "model_requests": [
                    _export_value(observation)
                    for observation in await list_model_request_observations(
                        scope=run_scope
                    )
                ],
            }
        )

    plans = await JvSpatialPlanStore(scope=scope).get_items()
    return {
        "session": session.model_dump(mode="json"),
        "plans": [item.model_dump(mode="json") for item in plans],
        "runs": exported_runs,
    }


async def purge_harness_sessions_for_thread(
    *, thread_id: str, principal_id: str, workspace_id: str
) -> int:
    """Erase private native-session state before hard-deleting its ChatThread.

    This deletes framework transcripts/checkpoints, plans, model observations,
    effect records, and rooted HarnessSession nodes for the exact owner and
    workspace. Core AgentRun/RunStep and usage/accounting records are retained
    under their independent audit and commercial retention policies.
    """
    if not graph_transaction_available():
        raise OperationTransactionUnavailable(
            "Harness session deletion requires a transactional shared store"
        )

    deleted = 0
    async with postgres_graph_transaction():
        thread = await ChatThread.get(thread_id)
        if thread is None:
            return 0
        owner_matches = thread.user_id == principal_id
        workspace_matches = thread.workspace_id == workspace_id
        if not owner_matches or not workspace_matches:
            raise InsufficientPermissionsError(message="Thread scope mismatch")

        from app.agentive.harness.turn_input import (
            purge_turn_input_capsules_for_thread,
        )

        deleted += await purge_turn_input_capsules_for_thread(
            principal_id=principal_id,
            workspace_id=workspace_id,
            thread_id=thread.id,
        )

        sessions = await HarnessSession.find({"thread_id": thread.id})
        for session in sessions:
            if (
                session.principal_id != principal_id
                or session.workspace_id != workspace_id
            ):
                raise ResourceConflictError(
                    message="Harness session scope is inconsistent",
                    details={"reason": "harness_session_scope_mismatch"},
                )
            namespace = "\0".join(
                (workspace_id, principal_id, thread.id, session.session_id)
            )
            scope_key = hashlib.sha256(namespace.encode("utf-8")).hexdigest()
            deleted += await _purge_session_payload_records(scope_key)
            await session.delete()
            deleted += 1

        thread.active_harness_session_id = None
        await thread.save()
    return deleted


async def _purge_session_payload_records(scope_key: str) -> int:
    """Delete encrypted session payload Objects, preserving audit/accounting."""
    record_types = (
        HarnessRunRecord,
        HarnessEventRecord,
        HarnessSnapshotRecord,
        HarnessCheckpointManifestRecord,
        HarnessToolEffectRecord,
        HarnessModelRequestRecord,
    )
    deleted = 0
    for record_type in record_types:
        records = await record_type.find({"scope_key": scope_key})
        for record in records:
            await record.delete()
            deleted += 1
    plan_id = (
        "o.HarnessPlanState." + hashlib.sha256(f"{scope_key}:plan".encode()).hexdigest()
    )
    plan = await HarnessPlanState.get(plan_id)
    if plan is not None:
        await plan.delete()
        deleted += 1
    return deleted


async def purge_expired_harness_sessions(
    *, retention_days: int, batch_size: int = 100, now: datetime | None = None
) -> dict[str, int]:
    """Purge private state only for terminal sessions older than the policy.

    Active and suspended sessions are never eligible. Each session's records
    and rooted graph node are removed in one PostgreSQL transaction. Core
    AgentRun/RunStep and usage/accounting records have separate retention and
    are intentionally left untouched.
    """
    if retention_days < 1:
        raise ValueError("Harness session retention must be at least one day")
    if batch_size < 1 or batch_size > 1000:
        raise ValueError("Harness retention batch size must be between 1 and 1000")
    if not graph_transaction_available():
        raise OperationTransactionUnavailable(
            "Harness retention requires a transactional shared store"
        )

    from jvspatial.core.context import get_default_context

    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days)
    cutoff_iso = cutoff.astimezone(timezone.utc).isoformat()
    context = get_default_context()
    candidates = await context.find(
        HarnessSession,
        {
            "context.status": {"$in": ["closed", "expired", "revoked"]},
            "context.updated_at": {"$lt": cutoff_iso},
        },
        limit=batch_size,
    )
    report = {"sessions_deleted": 0, "records_deleted": 0}

    for candidate in candidates:
        async with postgres_graph_transaction():
            session = await HarnessSession.get(candidate.id)
            if session is None or session.status not in {
                "closed",
                "expired",
                "revoked",
            }:
                continue
            if not session.updated_at:
                continue
            updated_at = datetime.fromisoformat(
                session.updated_at.replace("Z", "+00:00")
            )
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            if updated_at >= cutoff:
                continue

            thread = await ChatThread.get(session.thread_id)
            if (
                thread is None
                or thread.user_id != session.principal_id
                or thread.workspace_id != session.workspace_id
                or thread.active_harness_session_id == session.id
            ):
                # Retention is destructive; inconsistent scope/root state is
                # surfaced for repair instead of being guessed through.
                raise ResourceConflictError(
                    message="Expired Harness session failed scope validation",
                    details={"reason": "harness_retention_scope_mismatch"},
                )

            context = await thread.get_context()
            links = await context.find_edges_between(
                thread.id, session.id, edge_class=HAS_HARNESS_SESSION
            )
            if not links:
                raise ResourceConflictError(
                    message="Expired Harness session is detached from its thread",
                    details={"reason": "harness_retention_edge_missing"},
                )

            namespace = "\0".join(
                (
                    session.workspace_id,
                    session.principal_id,
                    session.thread_id,
                    session.session_id,
                )
            )
            scope_key = hashlib.sha256(namespace.encode("utf-8")).hexdigest()
            report["records_deleted"] += await _purge_session_payload_records(scope_key)
            await session.delete()
            report["sessions_deleted"] += 1

    return report
