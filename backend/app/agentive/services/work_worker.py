"""Leased work worker — static handlers + supervised heartbeat."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional

from app.agentive.services import work_execution, work_items
from app.agentive.work_models import WorkItem
from app.schemas.agentive.work import WorkError, WorkFailure

log = logging.getLogger(__name__)

DEFAULT_LEASE_SECONDS = work_items.DEFAULT_LEASE_SECONDS
HANDLED_KINDS = frozenset(
    {
        "capability",
        "routine_turn",
        "approval_resume",
        "event_trigger",
        "migration",
        "app_lifecycle",
    }
)

# Optional crash injection for chaos tests (process-local only).
_CRASH_POINT: Optional[str] = None

# Test-only handler registry keyed by work_item_id (callables are not JSON-safe).
_TEST_HANDLERS: dict[str, Callable[..., Awaitable[Any]]] = {}


def set_crash_point(name: Optional[str]) -> None:
    global _CRASH_POINT
    _CRASH_POINT = name


def register_test_handler(
    work_item_id: str, handler: Callable[..., Awaitable[Any]]
) -> None:
    _TEST_HANDLERS[work_item_id] = handler


def clear_test_handlers() -> None:
    _TEST_HANDLERS.clear()


def _maybe_crash(point: str) -> None:
    if _CRASH_POINT == point:
        import os

        os._exit(77)


async def _heartbeat_loop(
    work_item_id: str,
    *,
    lease_token: str,
    lease_fence: int,
    worker_id: str,
    lease_seconds: float,
    stop: asyncio.Event,
) -> None:
    interval = work_items.recommended_heartbeat_interval(lease_seconds)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except asyncio.TimeoutError:
            pass
        try:
            await work_items.heartbeat_lease(
                work_item_id,
                lease_token=lease_token,
                lease_fence=lease_fence,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
            )
        except WorkError as exc:
            if exc.code == "work.lease_lost":
                stop.set()
                return
            log.warning("work heartbeat failed: %s", exc)


async def _with_supervised_heartbeat(
    item: WorkItem,
    *,
    worker_id: str,
    lease_seconds: float,
    body: Callable[[], Awaitable[Any]],
) -> Any:
    stop = asyncio.Event()
    hb = asyncio.create_task(
        _heartbeat_loop(
            item.work_item_id,
            lease_token=item.lease_token,
            lease_fence=int(item.lease_fence or 0),
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            stop=stop,
        )
    )
    try:
        return await body()
    finally:
        stop.set()
        try:
            await hb
        except Exception:  # noqa: BLE001
            pass


async def _recheck_principal_workspace(item: WorkItem) -> None:
    if not (item.principal_id or "").strip() or not (item.workspace_id or "").strip():
        raise WorkError(
            "work.policy_denied",
            "principal_id and workspace_id required",
        )


async def _handle_capability(
    item: WorkItem,
    *,
    worker_id: str,
    lease_seconds: float,
) -> WorkItem:
    from app.agentive.services.capability_broker import invoke_declared_capability
    from app.agentive.services.execution_runs import start_run

    await _recheck_principal_workspace(item)
    payload = dict(item.input_payload or {})
    capability_key = str(payload.get("capability_key") or "").strip()
    if not capability_key:
        raise WorkError("work.permanent", "capability_key missing from input")

    logical = work_execution.logical_step_key_for(kind="capability")
    ctx = work_execution.build_work_execution_context(
        work_item=item, logical_step_key=logical
    )
    run = await start_run(
        thread_id=item.thread_id or "",
        user_id=item.principal_id,
        workspace_id=item.workspace_id,
        provider_id="work-worker",
        origin="http",
        app_id=item.app_id or None,
        run_id=ctx.run_id,
        work_item_id=item.work_item_id,
        deadline_at=item.deadline_at or "",
        metadata={"work_item_id": item.work_item_id},
    )
    _maybe_crash("after_agent_run_create")

    async def _body() -> Any:
        _maybe_crash("before_capability_effect")
        await work_execution.assert_effect_boundary_allowed(ctx)
        result = await invoke_declared_capability(
            principal_id=item.principal_id,
            workspace_id=item.workspace_id,
            capability_key=capability_key,
            origin="http",
            source=str(payload.get("source") or "core"),
            op_class=str(payload.get("op_class") or "read"),
            arguments=dict(payload.get("arguments") or {}),
            run_id=run.run_id,
            app_id=item.app_id or None,
            work_execution_context=ctx,
        )
        _maybe_crash("after_effect_receipt_before_completion")
        return result

    result = await _with_supervised_heartbeat(
        item, worker_id=worker_id, lease_seconds=lease_seconds, body=_body
    )
    # Re-check lease before completion.
    await work_execution.assert_effect_boundary_allowed(ctx)
    if not getattr(result, "ok", False):
        code = str(getattr(result, "error_code", "") or "capability.adapter_failed")
        if code.startswith("work."):
            raise WorkError(code, str(getattr(result, "message", "") or code))
        return await work_items.schedule_retry(
            item.work_item_id,
            lease_token=item.lease_token,
            lease_fence=int(item.lease_fence or 0),
            failure=WorkFailure(
                class_="transient",
                code=code,
                message=str(getattr(result, "message", "") or code),
                retryable=True,
            ),
        )
    # Staged / approval-gated capabilities park via durable WorkApproval.
    receipt = getattr(result, "receipt", None)
    step_status = str(getattr(receipt, "status", "") or "")
    if step_status == "waiting_for_human":
        from app.agentive.services import work_approvals

        data = getattr(result, "data", None) or {}
        staging_token = ""
        if isinstance(data, dict):
            staging_token = str(
                data.get("token") or getattr(receipt, "approval_ref", "") or ""
            )
        if not staging_token:
            staging_token = str(getattr(result, "approval_ref", "") or "")
        # Prefer broker-persisted step approval_ref when present on receipt path.
        if not staging_token and isinstance(data, dict):
            staging_token = str(data.get("token") or "")
        if not staging_token:
            raise WorkError(
                "work.approval_required",
                "waiting_for_human without staging token",
            )
        approval, parked = await work_approvals.propose_work_approval_unit(
            work_item_id=item.work_item_id,
            lease_token=item.lease_token,
            lease_fence=int(item.lease_fence or 0),
            run_id=ctx.run_id,
            run_step_id=str(getattr(receipt, "step_key", "") or ""),
            staging_token=staging_token,
            staged_change_fields=dict(data) if isinstance(data, dict) else {},
            authority_digest=ctx.effect_key,
            expires_at=item.deadline_at or "",
        )
        _ = approval
        return parked
    data = getattr(result, "data", None)
    obligations = (
        list(data.get("remaining_obligations") or []) if isinstance(data, dict) else []
    )
    return await work_items.transition_leased(
        item.work_item_id,
        lease_token=item.lease_token,
        lease_fence=int(item.lease_fence or 0),
        expected_status="running",
        target="succeeded",
        fields={
            "result_fingerprint": ctx.effect_key,
            "receipt_refs": work_execution.receipt_refs_from_capability_result(result),
            "remaining_obligations": obligations,
        },
    )


async def _handle_routine_turn(
    item: WorkItem,
    *,
    worker_id: str,
    lease_seconds: float,
) -> WorkItem:
    """Execute routine body under lease; body injectable for tests."""
    await _recheck_principal_workspace(item)
    handler = _TEST_HANDLERS.get(item.work_item_id)
    logical = work_execution.logical_step_key_for(kind="routine_turn", ordinal=0)
    ctx = work_execution.build_work_execution_context(
        work_item=item, logical_step_key=logical
    )

    async def _body() -> None:
        _maybe_crash("during_routine_provider_turn")
        await work_execution.assert_effect_boundary_allowed(ctx)
        if handler is not None:
            await handler(item, ctx)
            return
        # Production path: invoke existing routine execution when wired (Task 8).
        from app.agentive.services import routine_tasks

        execute = getattr(routine_tasks, "execute_routine_turn_under_lease", None)
        if execute is None:
            raise WorkError(
                "work.non_replayable_effect",
                "routine_turn handler not wired",
            )
        await execute(item, ctx)

    await _with_supervised_heartbeat(
        item, worker_id=worker_id, lease_seconds=lease_seconds, body=_body
    )
    await work_execution.assert_effect_boundary_allowed(ctx)
    return await work_items.transition_leased(
        item.work_item_id,
        lease_token=item.lease_token,
        lease_fence=int(item.lease_fence or 0),
        expected_status="running",
        target="succeeded",
    )


async def _handle_routine_turn_safe(
    item: WorkItem,
    *,
    worker_id: str,
    lease_seconds: float,
) -> WorkItem:
    """Wrap routine turn so thread-busy becomes retry_wait under the lease."""
    try:
        return await _handle_routine_turn(
            item, worker_id=worker_id, lease_seconds=lease_seconds
        )
    except Exception as exc:  # noqa: BLE001
        # Lazy import — avoid scheduler import cycle at module load.
        from app.services.routine_task_scheduler import _TurnBusy

        if isinstance(exc, _TurnBusy):
            return await work_items.schedule_retry(
                item.work_item_id,
                lease_token=item.lease_token,
                lease_fence=int(item.lease_fence or 0),
                failure=WorkFailure(
                    class_="transient",
                    code="work.turn_busy",
                    message=str(getattr(exc, "reason", "") or exc),
                    retryable=True,
                ),
            )
        raise


async def _handle_approval_resume(
    item: WorkItem,
    *,
    worker_id: str,
    lease_seconds: float,
) -> WorkItem:
    await _recheck_principal_workspace(item)
    # Approval resume re-enters the original WorkItem after approve → queued.
    # Worker treats it like capability continue when input says so.
    return await _handle_capability(
        item, worker_id=worker_id, lease_seconds=lease_seconds
    )


async def _handle_event_trigger(
    item: WorkItem,
    *,
    worker_id: str,
    lease_seconds: float,
) -> WorkItem:
    await _recheck_principal_workspace(item)
    return await _handle_capability(
        item, worker_id=worker_id, lease_seconds=lease_seconds
    )


async def _handle_migration(
    item: WorkItem,
    *,
    worker_id: str,
    lease_seconds: float,
) -> WorkItem:
    """Run a declared schema migration through the durable work authority."""
    await _recheck_principal_workspace(item)
    payload = dict(item.input_payload or {})
    operational_model_id = str(payload.get("operational_model_id") or "").strip()
    manifest_fingerprint = str(payload.get("manifest_fingerprint") or "").strip()
    if not operational_model_id or not manifest_fingerprint:
        raise WorkError(
            "work.permanent",
            "migration work requires operational_model_id and manifest_fingerprint",
        )

    from app.models.nodes import OperationalModel
    from app.services.migrations.runner import (
        execute_migration_work_item,
        resolve_migration_work_scope,
    )

    profile = await OperationalModel.get(operational_model_id)
    if profile is None:
        raise WorkError(
            "work.permanent", "migration operational model no longer exists"
        )
    # Track-attached profiles are graph-scoped and intentionally need not
    # duplicate ``workspace_id`` on the model node.  The enqueuer already
    # derived the durable work scope from that attachment; re-derive it here
    # instead of treating an empty cache field as a cross-workspace mutation.
    profile_workspace_id, _, _ = await resolve_migration_work_scope(profile)
    if profile_workspace_id != item.workspace_id:
        raise WorkError("work.policy_denied", "migration workspace no longer matches")

    logical = work_execution.logical_step_key_for(kind="migration")
    ctx = work_execution.build_work_execution_context(
        work_item=item, logical_step_key=logical
    )

    async def _body() -> Any:
        await work_execution.assert_effect_boundary_allowed(ctx)
        return await execute_migration_work_item(
            published_cp=profile,
            expected_manifest_fingerprint=manifest_fingerprint,
            actor_id=item.principal_id,
        )

    result = await _with_supervised_heartbeat(
        item, worker_id=worker_id, lease_seconds=lease_seconds, body=_body
    )
    await work_execution.assert_effect_boundary_allowed(ctx)
    if str(result.get("status") or "") == "failed":
        return await work_items.transition_leased(
            item.work_item_id,
            lease_token=item.lease_token,
            lease_fence=int(item.lease_fence or 0),
            expected_status="running",
            target="failed",
            fields={
                "result_fingerprint": ctx.effect_key,
                "result_refs": [f"operational_model:{operational_model_id}"],
                "remaining_obligations": [
                    {
                        "kind": "migration_recovery",
                        "operational_model_id": operational_model_id,
                        "explanation": "One or more migration targets failed; inspect and retry the migration.",
                    }
                ],
                "failure": work_items.normalize_failure(
                    class_="permanent",
                    code="work.migration_failed",
                    message="migration completed with failed entries",
                    retryable=False,
                ),
            },
        )
    return await work_items.transition_leased(
        item.work_item_id,
        lease_token=item.lease_token,
        lease_fence=int(item.lease_fence or 0),
        expected_status="running",
        target="succeeded",
        fields={
            "result_fingerprint": ctx.effect_key,
            "result_refs": [f"operational_model:{operational_model_id}"],
        },
    )


async def _handle_app_lifecycle(
    item: WorkItem, *, worker_id: str, lease_seconds: float
) -> WorkItem:
    """Execute a queued package lifecycle action under its durable lease."""
    await _recheck_principal_workspace(item)
    payload = dict(item.input_payload or {})
    action = str(payload.get("action") or "").strip()
    from app.services import app_lifecycle

    async def _body() -> Any:
        if action == "install":
            return await app_lifecycle.install_app(
                workspace_id=item.workspace_id,
                library_cp_id=str(payload["library_cp_id"]),
                actor_id=item.principal_id,
                settings=payload.get("settings"),
                include_seed_data=bool(payload.get("include_seed_data", True)),
            )
        if action == "upgrade":
            return await app_lifecycle.update_app_from_library(
                app_id=item.app_id,
                actor_id=item.principal_id,
                version=payload.get("version"),
            )
        if action == "pause":
            return await app_lifecycle.pause_app(
                app_id=item.app_id, actor_id=item.principal_id
            )
        if action == "resume":
            return await app_lifecycle.resume_app(
                app_id=item.app_id, actor_id=item.principal_id
            )
        if action == "uninstall":
            return await app_lifecycle.uninstall_app(
                app_id=item.app_id,
                actor_id=item.principal_id,
                force=bool(payload.get("force", False)),
                archive=bool(payload.get("archive", True)),
            )
        if action == "finalize_install":
            return await app_lifecycle.finalize_install(
                app_id=item.app_id,
                actor_id=item.principal_id,
                install_token=str(payload["install_token"]),
                settings=dict(payload.get("settings") or {}),
            )
        raise WorkError(
            "work.permanent", f"unsupported app lifecycle action {action!r}"
        )

    result = await _with_supervised_heartbeat(
        item, worker_id=worker_id, lease_seconds=lease_seconds, body=_body
    )
    result_refs = [f"app_lifecycle:{action}"]
    result_app_id = (
        str(result.get("app_id") or "") if isinstance(result, dict) else ""
    ) or item.app_id
    if result_app_id:
        result_refs.append(f"app:{result_app_id}")
    return await work_items.transition_leased(
        item.work_item_id,
        lease_token=item.lease_token,
        lease_fence=int(item.lease_fence or 0),
        expected_status="running",
        target="succeeded",
        fields={"result_refs": result_refs},
    )


async def execute_claimed_work(
    item: WorkItem,
    *,
    worker_id: str = "work-worker",
    lease_seconds: float = DEFAULT_LEASE_SECONDS,
) -> WorkItem:
    """Run one leased WorkItem through the static handler table."""
    _maybe_crash("after_claim")
    kind = (item.kind or "").strip()
    if kind not in HANDLED_KINDS:
        return await work_items.transition_leased(
            item.work_item_id,
            lease_token=item.lease_token,
            lease_fence=int(item.lease_fence or 0),
            expected_status="running",
            target="failed",
            fields={
                "failure": work_items.normalize_failure(
                    class_="permanent",
                    code="work.unknown_kind",
                    message=f"unknown kind {kind!r}",
                    retryable=False,
                )
            },
        )
    try:
        if kind == "capability":
            return await _handle_capability(
                item, worker_id=worker_id, lease_seconds=lease_seconds
            )
        if kind == "routine_turn":
            return await _handle_routine_turn_safe(
                item, worker_id=worker_id, lease_seconds=lease_seconds
            )
        if kind == "approval_resume":
            return await _handle_approval_resume(
                item, worker_id=worker_id, lease_seconds=lease_seconds
            )
        if kind == "event_trigger":
            return await _handle_event_trigger(
                item, worker_id=worker_id, lease_seconds=lease_seconds
            )
        if kind == "migration":
            return await _handle_migration(
                item, worker_id=worker_id, lease_seconds=lease_seconds
            )
        if kind == "app_lifecycle":
            return await _handle_app_lifecycle(
                item, worker_id=worker_id, lease_seconds=lease_seconds
            )
    except WorkError as exc:
        if exc.code in {"work.lease_lost", "work.cancelled", "work.deadline_exceeded"}:
            # Leave item for recovery / cancel path — do not complete.
            raise
        retryable = exc.code.startswith("work.") and "transient" in exc.code
        failure = WorkFailure(
            class_="transient" if retryable else "permanent",
            code=exc.code,
            message=exc.message,
            retryable=retryable,
        )
        if retryable:
            return await work_items.schedule_retry(
                item.work_item_id,
                lease_token=item.lease_token,
                lease_fence=int(item.lease_fence or 0),
                failure=failure,
            )
        return await work_items.transition_leased(
            item.work_item_id,
            lease_token=item.lease_token,
            lease_fence=int(item.lease_fence or 0),
            expected_status="running",
            target="failed",
            fields={"failure": failure.model_dump(by_alias=True)},
        )
    raise WorkError("work.unknown_kind", f"unhandled kind {kind}")


async def process_one_due_item(
    *,
    worker_id: str = "work-worker",
    lease_seconds: float = DEFAULT_LEASE_SECONDS,
    work_item_id: Optional[str] = None,
) -> Optional[WorkItem]:
    """Claim one due item (optional id) and execute it."""
    claimed = await work_items.claim_due_candidate(
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        work_item_id=work_item_id,
    )
    if claimed is None:
        return None
    _maybe_crash("after_enqueue")
    return await execute_claimed_work(
        claimed, worker_id=worker_id, lease_seconds=lease_seconds
    )


async def worker_loop(
    *,
    worker_id: str = "work-worker",
    lease_seconds: float = DEFAULT_LEASE_SECONDS,
    idle_sleep: float = 0.5,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """Background poll loop. Stops when ``stop_event`` is set."""
    stop = stop_event or asyncio.Event()
    while not stop.is_set():
        try:
            result = await process_one_due_item(
                worker_id=worker_id, lease_seconds=lease_seconds
            )
            if result is None:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=idle_sleep)
                except asyncio.TimeoutError:
                    pass
        except WorkError as exc:
            log.info("work worker pass ended: %s", exc.code)
        except Exception:  # noqa: BLE001
            log.exception("work worker pass failed")
            await asyncio.sleep(idle_sleep)


__all__ = [
    "HANDLED_KINDS",
    "clear_test_handlers",
    "execute_claimed_work",
    "process_one_due_item",
    "register_test_handler",
    "set_crash_point",
    "worker_loop",
]
