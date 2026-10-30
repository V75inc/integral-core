"""Durable staging ownership and result fences; no implicit dispatch authority.

These primitives are PostgreSQL-only. A dispatched external effect is never
reclaimed on a clock: losing the worker means its outcome needs reconciliation.
Canonical creation and Entry mutations use these fences. Integration of other
executor families must preserve their event delivery and effect boundaries.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from contextlib import asynccontextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

from jvspatial.core import Object
from jvspatial.core.context import get_default_context

from app.agentive.staging import StagingError
from app.services.app_operations.transaction_scope import (
    current_graph_transaction_database,
    postgres_graph_transaction,
)


class UnappliedStagedCommand(Exception):
    """Carry a local API refusal through rollback, never an external outcome."""

    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__("Approved graph command was refused")
        self.result = result


class StagingExecutionRecord(Object):
    """Command-shaped receipt, never a resource grant or graph traversal node."""

    principal_id: str = ""
    workspace_id: str = ""
    kind: str = ""
    request_hash: str = ""
    owner: str = ""
    fence: int = 0
    state: str = "ready"
    expires_at: str = ""
    result_ciphertext: str = ""
    origin_ciphertext: str = ""
    proposal_ciphertext: str = ""
    local_effect_state: str = ""
    external_outcome: str = "not_dispatched"


@dataclass(frozen=True)
class StagingExecutionClaim:
    object_id: str
    principal_id: str
    workspace_id: str
    kind: str
    request_hash: str
    owner: str
    fence: int


@dataclass(frozen=True)
class BatchStepApproval:
    parent: StagingExecutionClaim
    token: str
    index: int
    references: dict[str, str]


@dataclass
class StagingCommandExecution:
    claim: StagingExecutionClaim
    token: str
    task: object
    completed: bool = False
    external: bool = False
    hooks_started: int = 0
    hooks_pending: int = 0
    hooks_failed: int = 0
    effect_depth: int = 0
    batch_step: BatchStepApproval | None = None
    approved_payload: dict[str, Any] = field(default_factory=dict)
    proposal_origin: dict[str, Any] | None = None
    proposal_snapshot: dict[str, Any] | None = None
    creation_setup: dict[str, Any] = field(default_factory=dict)
    event_outboxes: list[Any] = field(default_factory=list)


_command_execution: ContextVar[StagingCommandExecution | None] = ContextVar(
    "integral_staging_command_execution", default=None
)


@dataclass
class _LockedExecutions:
    database: Any
    task: object
    claims: set[StagingExecutionClaim] = field(default_factory=set)


_locked_executions: ContextVar[_LockedExecutions | None] = ContextVar(
    "integral_locked_staging_executions", default=None
)


@asynccontextmanager
async def bind_staging_command_execution(
    claim: StagingExecutionClaim,
    token: str,
    *,
    external: bool = False,
    batch_step: BatchStepApproval | None = None,
):
    """Bind one immutable approved command to this dispatcher task only."""
    if _command_execution.get() is not None:
        raise StagingError(
            "execution_binding_conflict", "An approved command is already bound"
        )
    bound = StagingCommandExecution(
        claim, token, asyncio.current_task(), external=external, batch_step=batch_step
    )
    reset = _command_execution.set(bound)
    try:
        yield bound
    finally:
        _command_execution.reset(reset)


def assert_staging_hook_dispatch_allowed() -> None:
    """Unknown hook I/O cannot run inside an atomic local command."""
    bound = _command_execution.get()
    if bound is not None and (
        bound.task is not asyncio.current_task()
        or (not bound.completed and not bound.external)
    ):
        raise StagingError(
            "external_hook_dispatch_required",
            "This approved change has a hook that requires an external dispatch boundary",
        )


def begin_staging_hook() -> StagingCommandExecution | None:
    """Track an actual extension invocation under a persisted dispatch intent."""
    assert_staging_hook_dispatch_allowed()
    bound = _command_execution.get()
    if bound is not None:
        bound.hooks_started += 1
        bound.hooks_pending += 1
    return bound


def finish_staging_hook(bound: StagingCommandExecution | None, *, failed: bool) -> None:
    """A failed or interrupted extension never becomes a verified success."""
    if bound is not None:
        bound.hooks_pending -= 1
        bound.hooks_failed += int(failed)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def execution_record_id(token: str) -> str:
    """Return an opaque stable record identity without persisting the token."""
    return "o.StagingExecutionRecord." + hashlib.sha256(token.encode()).hexdigest()


async def claim_staging_execution(
    *,
    token: str,
    principal_id: str,
    workspace_id: str,
    kind: str,
    payload: dict[str, Any],
    lease_seconds: float = 60,
) -> StagingExecutionClaim:
    """Claim immutable command intent, without authorizing its resource writes.

    Only an expired *undispatched* claim can be replaced. Local effects must use
    ``staging_execution_effect``; external dispatch must mark intent first.
    """
    if not all((token, principal_id, workspace_id, kind)) or lease_seconds <= 0:
        raise ValueError("Complete command identity and a positive lease are required")
    digest = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()
    identity = {
        "principal_id": principal_id,
        "workspace_id": workspace_id,
        "kind": kind,
        "request_hash": digest,
    }
    oid = execution_record_id(token)
    async with postgres_graph_transaction(get_default_context().database) as database:
        await StagingExecutionRecord.create_if_absent(id=oid, **identity)
        # Scalar no-op CAS locks the row before examining any mutable state.
        row = await database.find_one_and_update(
            "object",
            {"id": oid, "entity": StagingExecutionRecord._entity_name()},
            {"$set": {}},
        )
        current = (row or {}).get("context") or {}
        if any(current.get(key) != value for key, value in identity.items()):
            raise StagingError(
                "execution_identity_conflict", "Approved command identity changed"
            )
        state = current.get("state")
        if state in {"dispatched", "applied", "unknown"}:
            raise StagingError(
                "application_reconciliation_required",
                "Reconcile the existing execution; do not replay it",
            )
        if state == "claimed":
            expiry = datetime.fromisoformat(current["expires_at"])
            if expiry > _now():
                raise StagingError(
                    "already_executing", "This approved change is already being applied"
                )
        elif state != "ready":
            raise StagingError(
                "execution_state_invalid", "Approved execution state is unavailable"
            )
        owner = secrets.token_hex(24)
        fence = int(current["fence"]) + 1
        await database.find_one_and_update(
            "object",
            {"id": oid},
            {
                "$set": {
                    "context.owner": owner,
                    "context.fence": fence,
                    "context.state": "claimed",
                    "context.expires_at": (
                        _now() + timedelta(seconds=lease_seconds)
                    ).isoformat(),
                }
            },
        )
        return StagingExecutionClaim(oid, **identity, owner=owner, fence=fence)


def _claim_identity_query(claim: StagingExecutionClaim) -> dict[str, Any]:
    return {
        "id": claim.object_id,
        "entity": StagingExecutionRecord._entity_name(),
        **{
            f"context.{key}": getattr(claim, key)
            for key in (
                "principal_id",
                "workspace_id",
                "kind",
                "request_hash",
                "owner",
                "fence",
            )
        },
    }


async def _lock_current(
    database: Any, claim: StagingExecutionClaim, *, dispatched: bool = False
) -> dict[str, Any]:
    row = await database.find_one_and_update(
        "object", _claim_identity_query(claim), {"$set": {}}
    )
    current = (row or {}).get("context") or {}
    held = _locked_executions.get()
    same_scope = (
        held is not None
        and held.database is database
        and held.task is asyncio.current_task()
    )
    admitted = held is not None and same_scope and claim in held.claims
    if (
        row is None
        or current.get("state") != ("dispatched" if dispatched else "claimed")
        or (
            not dispatched
            and not admitted
            and datetime.fromisoformat(current["expires_at"]) <= _now()
        )
    ):
        raise StagingError(
            "execution_claim_lost", "This worker no longer owns the approved execution"
        )
    if held is not None and same_scope:
        held.claims.add(claim)
    return current


@asynccontextmanager
async def staging_execution_effect(
    claim: StagingExecutionClaim, *, dispatched: bool = False
) -> AsyncIterator[Any]:
    """Lock the command fence in the same transaction as local graph effects.

    The caller must perform current authorization and record its result before
    leaving. Exceptions/cancellation roll effects and completion back together.
    """
    async with postgres_graph_transaction(get_default_context().database) as database:
        # Lease expiry fences admission. Once admitted, PostgreSQL's retained
        # row lock fences this task through effect/receipt commit, even if a
        # large command outlasts the lease. Never extend that proof to another
        # task, transaction, owner or fence, or to unadmitted batch claims.
        parent = _locked_executions.get()
        held = (
            parent
            if parent is not None
            and parent.database is database
            and parent.task is asyncio.current_task()
            else _LockedExecutions(database, asyncio.current_task())
        )
        reset = _locked_executions.set(held)
        try:
            await _lock_current(database, claim, dispatched=dispatched)
            yield database
            row = await database.get("object", claim.object_id)
            current = (row or {}).get("context") or {}
            if current.get("local_effect_state") != "applied" or not current.get(
                "result_ciphertext"
            ):
                raise StagingError(
                    "execution_receipt_required",
                    "Local effects cannot commit without their result receipt",
                )
        finally:
            _locked_executions.reset(reset)


async def record_staging_execution_result(
    *,
    claim: StagingExecutionClaim,
    database: Any,
    result: dict[str, Any],
    dispatched: bool = False,
    origin_proof: dict[str, Any] | None = None,
    proposal_snapshot: dict[str, Any] | None = None,
) -> None:
    """Commit an encrypted result alongside local graph effects, on the same task."""
    from app.services.credential_crypto import encrypt_secret_for_storage

    if database is not current_graph_transaction_database():
        raise StagingError(
            "execution_transaction_required", "Result must share its effect transaction"
        )
    await _lock_current(database, claim, dispatched=dispatched)
    stored = encrypt_secret_for_storage(
        json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False),
        aad=claim.object_id,
    )
    origin_ciphertext = ""
    proposal_ciphertext = ""
    if origin_proof is not None:
        origin_ciphertext = encrypt_secret_for_storage(
            json.dumps(
                origin_proof, sort_keys=True, separators=(",", ":"), allow_nan=False
            ),
            aad=claim.object_id + ":proposal-origin",
        )
    if origin_proof is not None and proposal_snapshot is not None:
        proposal_ciphertext = encrypt_secret_for_storage(
            json.dumps(
                proposal_snapshot,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ),
            aad=claim.object_id + ":proposal",
        )
    await database.find_one_and_update(
        "object",
        {"id": claim.object_id},
        {
            "$set": {
                "context.state": "dispatched" if dispatched else "applied",
                "context.local_effect_state": "applied",
                "context.result_ciphertext": stored,
                "context.origin_ciphertext": origin_ciphertext,
                "context.proposal_ciphertext": proposal_ciphertext,
            }
        },
    )


async def mark_staging_external_dispatch(claim: StagingExecutionClaim) -> None:
    """Persist irreversible dispatch intent before sending any external bytes."""
    async with postgres_graph_transaction(get_default_context().database) as database:
        await _lock_current(database, claim)
        await database.find_one_and_update(
            "object",
            {"id": claim.object_id},
            {
                "$set": {
                    "context.state": "dispatched",
                    "context.external_outcome": "unknown",
                }
            },
        )


async def finish_staging_external_dispatch(claim: StagingExecutionClaim) -> None:
    """Record returned extension calls; never infer this after worker loss."""
    async with postgres_graph_transaction(get_default_context().database) as database:
        current = await _lock_current(database, claim, dispatched=True)
        if current.get("local_effect_state") != "applied":
            raise StagingError(
                "execution_receipt_required", "The local effect result is unavailable"
            )
        await database.find_one_and_update(
            "object",
            {"id": claim.object_id},
            {
                "$set": {
                    "context.state": "applied",
                    "context.external_outcome": "returned",
                }
            },
        )


async def renew_staging_execution(claim: StagingExecutionClaim) -> None:
    """Keep an undispatched batch owner current between its committed steps."""
    async with postgres_graph_transaction(get_default_context().database) as database:
        await _lock_current(database, claim)
        await database.find_one_and_update(
            "object",
            {"id": claim.object_id},
            {
                "$set": {
                    "context.expires_at": (_now() + timedelta(seconds=60)).isoformat(),
                }
            },
        )


async def load_staging_execution_result(
    *,
    token: str,
    principal_id: str,
    workspace_id: str,
    kind: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Read exact private effect evidence, never an executable approval grant."""
    from app.services.credential_crypto import decrypt_secret_from_storage

    oid = execution_record_id(token)
    # Read the native transaction store rather than a caller's entity cache.
    # A stale "claimed" cache must not turn a committed result into a retry.
    async with postgres_graph_transaction(get_default_context().database) as database:
        row = await database.get("object", oid)
    if row is None:
        return None
    current = row.get("context") or {}
    digest = hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    if row.get("entity") != StagingExecutionRecord._entity_name() or any(
        current.get(key) != value
        for key, value in {
            "principal_id": principal_id,
            "workspace_id": workspace_id,
            "kind": kind,
            "request_hash": digest,
        }.items()
    ):
        raise StagingError(
            "execution_identity_conflict", "Approved command identity changed"
        )
    if (
        current.get("state") != "applied"
        and current.get("local_effect_state") != "applied"
    ):
        return None
    try:
        raw = decrypt_secret_from_storage(current["result_ciphertext"], aad=oid)
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("Invalid result")
        if current.get("external_outcome") not in {None, "not_dispatched", "returned"}:
            result["_external_outcome"] = current["external_outcome"]
        return result
    except Exception as exc:
        raise StagingError(
            "execution_receipt_unavailable",
            "The saved effect result needs reconciliation",
        ) from exc


async def release_unapplied_staging_claim(
    claim: StagingExecutionClaim, *, dispatched: bool = False
) -> None:
    """Release only an exact undispatched owner after verified local rollback."""
    async with postgres_graph_transaction(get_default_context().database) as database:
        # A verified rollback must remain releasable after lease expiry. This
        # admits no effect: lock the exact unchanged owner/fence and refuse
        # completed results. Keep partial batch evidence for fenced step recovery.
        row = await database.find_one_and_update(
            "object",
            {
                **_claim_identity_query(claim),
                "context.state": "dispatched" if dispatched else "claimed",
            },
            {"$set": {}},
        )
        if row is None:
            raise StagingError(
                "execution_claim_lost",
                "This worker no longer owns the approved execution",
            )
        current = row.get("context") or {}
        if current.get("local_effect_state") == "applied" or current.get(
            "result_ciphertext"
        ):
            raise StagingError(
                "application_reconciliation_required",
                "A committed effect cannot be released for retry",
            )
        await database.find_one_and_update(
            "object",
            {"id": claim.object_id},
            {
                "$set": {
                    "context.state": "ready",
                    "context.owner": "",
                    "context.expires_at": "",
                    "context.external_outcome": "not_dispatched",
                }
            },
        )


@asynccontextmanager
async def staged_command_effect(
    *,
    user_id: str,
    workspace_id: str,
    kind: str,
    targets: dict[str, Any],
):
    """Join a canonical command and require its exact approved decision."""
    bound = _command_execution.get()
    if bound is None:
        yield None
        return
    if bound.task is not asyncio.current_task():
        raise StagingError(
            "execution_binding_conflict",
            "Approved command belongs to another task",
        )
    if bound.completed:
        # Post-commit follow-ups retain their own canonical command boundary.
        yield None
        return
    if bound.effect_depth:
        # Trusted hook children keep their own canonical authority but join
        # the outer graph commit. Only the root writes the command receipt.
        yield None
        return
    claim = bound.claim
    if claim.principal_id != user_id:
        from app.services.identity_admission import resolve_current_profile

        approved_principal = await resolve_current_profile(claim.principal_id)
        acting_principal = await resolve_current_profile(user_id)
        if approved_principal.id != acting_principal.id:
            raise StagingError(
                "execution_binding_conflict", "Approved Entry principal changed"
            )
    if (
        bound.task is not asyncio.current_task()
        or claim.kind != kind
        or claim.workspace_id != workspace_id
    ):
        raise StagingError(
            "execution_binding_conflict", "Approved command binding changed"
        )
    async with staging_execution_effect(claim, dispatched=bound.external) as database:
        from app.agentive.staging_store import StagedChangeRecord

        approval_token = (
            bound.batch_step.token if bound.batch_step is not None else bound.token
        )
        if bound.batch_step is not None:
            await _lock_current(database, bound.batch_step.parent)
        rows = await database.find(
            "object",
            {
                "entity": StagedChangeRecord._entity_name(),
                "context.token": approval_token,
            },
            limit=2,
        )
        if len(rows) != 1:
            raise StagingError(
                "approval_unavailable", "Current approved decision is unavailable"
            )
        row = await database.find_one_and_update(
            "object", {"id": rows[0]["id"]}, {"$set": {}}
        )
        decision = (row or {}).get("context") or {}
        digest = hashlib.sha256(
            json.dumps(
                decision.get("payload"),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
        expiry = datetime.fromisoformat(decision.get("expires_at") or "")
        approved_kind = claim.kind
        approved_hash = claim.request_hash
        approved_payload = decision.get("payload") or {}
        if bound.batch_step is not None:
            from app.agentive.staging_executors import _resolve_batch_refs

            approved_kind = "batch"
            approved_hash = bound.batch_step.parent.request_hash
            operations = approved_payload.get("operations") or []
            index = bound.batch_step.index
            if (
                not isinstance(operations, list)
                or index < 0
                or index >= len(operations)
            ):
                raise StagingError(
                    "approval_unavailable", "Approved batch step is unavailable"
                )
            operation = operations[index]
            approved_payload = _resolve_batch_refs(
                operation.get("payload") or {}, bound.batch_step.references
            )
            child_hash = hashlib.sha256(
                json.dumps(
                    approved_payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            ).hexdigest()
            if operation.get("kind") != claim.kind or child_hash != claim.request_hash:
                raise StagingError(
                    "approval_unavailable", "Approved batch step input changed"
                )
        if (
            decision.get("state") != "blessed"
            or decision.get("user_id") != claim.principal_id
            or decision.get("kind") != approved_kind
            or digest != approved_hash
            or expiry <= _now()
            or (
                decision.get("workspace_id")
                and decision["workspace_id"] != workspace_id
            )
            or any(
                (
                    approved_payload.get(key) or None
                    if key == "app_id"
                    else (
                        (
                            approved_payload.get("text")
                            or approved_payload.get("body")
                            or ""
                        )
                        if key == "text" and claim.kind == "edit_comment"
                        else approved_payload.get(key)
                    )
                )
                != value
                for key, value in targets.items()
            )
        ):
            raise StagingError(
                "approval_unavailable",
                "Current approved decision does not match this effect",
            )

        from app.agentive.services.entry_proposal_revision import (
            validate_entry_edit_fence,
        )

        await validate_entry_edit_fence(
            decision, bound.batch_step.index if bound.batch_step is not None else 0
        )

        async def complete(result: dict[str, Any]) -> None:
            await record_staging_execution_result(
                claim=claim,
                database=database,
                result=result,
                dispatched=bound.external,
                **(
                    {
                        "origin_proof": bound.proposal_origin,
                        "proposal_snapshot": bound.proposal_snapshot,
                    }
                    if bound.proposal_origin is not None
                    else {}
                ),
            )
            if bound.batch_step is not None:
                # A child graph commit also leaves durable evidence on its
                # parent. Losing the worker before any progress update cannot
                # make expiry erase a partially-applied batch.
                await _lock_current(database, bound.batch_step.parent)
                await database.find_one_and_update(
                    "object",
                    {"id": bound.batch_step.parent.object_id},
                    {"$set": {"context.local_effect_state": "partial"}},
                )
            bound.completed = True

        bound.approved_payload = deepcopy(approved_payload)
        bound.proposal_origin = deepcopy(decision.get("origin_proof"))
        bound.proposal_snapshot = (
            deepcopy(decision) if bound.proposal_origin is not None else None
        )
        bound.effect_depth += 1
        try:
            yield complete
        finally:
            bound.effect_depth -= 1


@asynccontextmanager
async def staged_entry_effect(
    *, user_id: str, workspace_id: str, track_id: str, entry_id: str = ""
):
    """Bind the canonical Entry target to the common command receipt fence."""
    async with staged_command_effect(
        user_id=user_id,
        workspace_id=workspace_id,
        kind="update_entry" if entry_id else "create_entry",
        targets={"entry_id": entry_id} if entry_id else {"track_id": track_id},
    ) as complete:
        yield complete


def staged_creation_setup_result(kind: str) -> dict[str, Any] | None:
    """Only this completed dispatcher task can skip duplicate inline setup."""
    bound = _command_execution.get()
    if bound is None:
        return None
    if bound.task is not asyncio.current_task():
        raise StagingError(
            "execution_binding_conflict", "Command belongs to another task"
        )
    if bound.claim.kind == kind and bound.completed:
        return dict(bound.creation_setup)
    return None


async def apply_bound_creation_setup(
    kind: str, result: Any, user_id: str
) -> dict[str, Any]:
    """Apply the immutable Track schema/taxonomy inside its graph receipt fence."""
    bound = _command_execution.get()
    if bound is None or bound.claim.kind != kind or kind != "create_track":
        return {}
    if bound.task is not asyncio.current_task() or not bound.effect_depth:
        raise StagingError(
            "execution_binding_conflict", "Creation setup requires its effect fence"
        )
    from app.services.staged_creation_setup import apply_track_setup

    payload = bound.approved_payload
    bound.creation_setup = await apply_track_setup(
        user_id=user_id,
        workspace_id=bound.claim.workspace_id,
        track_id=result.id,
        entry_types=payload.get("entry_types"),
        taxonomy=payload.get("taxonomy"),
    )
    return bound.creation_setup


async def capture_staging_change_event(event: dict[str, Any]) -> bool:
    """Commit staged command events on the graph store before network delivery."""
    bound = _command_execution.get()
    if bound is None or not bound.effect_depth:
        return False
    from app.agentive.services.staging_command_apply import CANONICAL_COMMAND_KINDS

    if bound.claim.kind not in CANONICAL_COMMAND_KINDS:
        return False
    if bound.task is not asyncio.current_task():
        raise StagingError(
            "execution_binding_conflict", "Event belongs to another task"
        )
    from app.services.app_operations.event_outbox import insert_revision_event
    from app.services.resource_location import KNOWN_APP_STATES, locate_resource

    scope = str(event.get("scope") or "").split(":", 1)
    location = (
        await locate_resource(*scope, allowed_app_states=KNOWN_APP_STATES)
        if len(scope) == 2
        else None
    )
    if len(scope) == 2 and scope[0] == "operational_model":
        # A model event must belong to this command's canonical selected owner.
        # An arbitrary model id is never evidence of workspace containment.
        from app.agentive.services.staging_model_commands import MODEL_COMMAND_KINDS
        from app.services.attached_model_scope import read_attached_model

        if bound.claim.kind in MODEL_COMMAND_KINDS:
            payload = bound.approved_payload
            owner_kind = "track" if payload.get("track_id") else "app"
            owner_id = str(payload.get(f"{owner_kind}_id") or "")
            attached = await read_attached_model(owner_kind, owner_id)
            if attached is not None and attached[1].id == scope[1]:
                location = await locate_resource(
                    owner_kind, owner_id, allowed_app_states=KNOWN_APP_STATES
                )
    routine_witness = False
    if scope == ["workspace", bound.claim.workspace_id]:
        from app.agentive.nodes import RoutineTask
        from app.agentive.services.staging_routine_commands import ROUTINE_COMMAND_KINDS

        if (
            bound.claim.kind in ROUTINE_COMMAND_KINDS
            and event.get("resource_type") == "RoutineTask"
        ):
            routine = await RoutineTask.find_one({"id": event.get("resource_id")})
            routine_witness = (
                routine is not None
                and routine.user_id == bound.claim.principal_id
                and routine.workspace_id == bound.claim.workspace_id
                and event.get("actor_id") == bound.claim.principal_id
                and event.get("action")
                == {"routine_task_cancel": "routine_task.delete"}.get(
                    bound.claim.kind,
                    bound.claim.kind.replace("routine_task_", "routine_task."),
                )
            )
    before = event.get("before")
    if bound.claim.kind == "routine_task_purge" and scope == [
        "workspace",
        bound.claim.workspace_id,
    ]:
        routine_witness = (
            isinstance(before, dict)
            and before.get("id") == bound.approved_payload.get("routine_id")
            and before.get("id") == event.get("resource_id")
            and before.get("workspace_id") == bound.claim.workspace_id
            and event.get("actor_id") == bound.claim.principal_id
            and event.get("action") == "routine_task.purge"
            and event.get("resource_type") == "RoutineTask"
        )
    deleted_witness = (
        location is None
        and isinstance(before, dict)
        and (
            event.get("action", "").endswith(".delete")
            or event.get("action") in {"anchor.cascade", "app.uninstalled"}
        )
        and before.get("id") == event.get("resource_id")
        and before.get("workspace_id") == bound.claim.workspace_id
    )
    if (
        not deleted_witness
        and not routine_witness
        and (location is None or location.workspace_id != bound.claim.workspace_id)
    ):
        raise StagingError(
            "event_scope_invalid", "Command event is outside its admitted workspace"
        )
    outbox = await insert_revision_event(
        transaction=current_graph_transaction_database(),
        workspace_id=bound.claim.workspace_id,
        namespace="staging-command-v1",
        subject_id=bound.claim.object_id,
        revision=len(bound.event_outboxes) + 1,
        event=event,
    )
    bound.event_outboxes.append(outbox)
    return True


async def deliver_staging_command_events() -> None:
    """Offer committed outboxes after the canonical command transaction exits."""
    bound = _command_execution.get()
    if bound is None or not bound.completed:
        return
    from app.services.workspace_membership import _deliver

    for outbox in bound.event_outboxes:
        await _deliver(outbox)


def staged_command_completed(kind: str) -> bool:
    """Return completion only for the command bound to this dispatcher task."""
    bound = _command_execution.get()
    if bound is None:
        return False
    if bound.task is not asyncio.current_task():
        raise StagingError(
            "execution_binding_conflict", "Command belongs to another task"
        )
    return bound.claim.kind == kind and bound.completed


def staged_command_preconditions() -> dict[str, Any]:
    """Read immutable approval preconditions inside this task's effect fence."""
    bound = _command_execution.get()
    if bound is None:
        return {}
    if bound.task is not asyncio.current_task() or not bound.effect_depth:
        raise StagingError(
            "execution_binding_conflict", "Preconditions require the effect fence"
        )
    return {
        key: bound.approved_payload[key]
        for key in ("expected_record_revision", "expected_schema_revision")
        if key in bound.approved_payload
    }


def staged_command_bound(kind: str) -> bool:
    """Identify the current dispatcher command without granting its authority."""
    bound = _command_execution.get()
    if bound is None:
        return False
    if bound.task is not asyncio.current_task():
        raise StagingError(
            "execution_binding_conflict", "Command belongs to another task"
        )
    return bound.claim.kind == kind and not bound.completed


def staged_cleanup_generation(app_id: str) -> str | None:
    """Share one cleanup generation across a held App command's nested phases."""
    bound = _command_execution.get()
    if bound is None or bound.claim.kind != "delete_app":
        return None
    if bound.task is not asyncio.current_task() or not bound.effect_depth:
        raise StagingError(
            "execution_binding_conflict", "Cleanup requires its App effect fence"
        )
    return bound.claim.object_id
