"""Leased work worker — static handlers + supervised heartbeat."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional

from app.agentive.services import work_execution, work_items
from app.agentive.work_models import WorkItem
from app.exceptions import AppDependencyError, AppUninstallBlockedError
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
    lease_lost: asyncio.Event,
    failure_code: dict[str, str],
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
            log.warning("work heartbeat failed: %s", exc.code)
            failure_code["code"] = (
                exc.code
                if exc.code in {"work.cancelled", "work.deadline_exceeded"}
                else "work.lease_lost"
            )
            lease_lost.set()
            return
        except Exception:  # noqa: BLE001
            log.exception("work heartbeat failed; stopping the leased handler")
            failure_code["code"] = "work.lease_lost"
            lease_lost.set()
            return


async def _with_supervised_heartbeat(
    item: WorkItem,
    *,
    worker_id: str,
    lease_seconds: float,
    body: Callable[[], Awaitable[Any]],
) -> Any:
    stop = asyncio.Event()
    lease_lost = asyncio.Event()
    failure_code = {"code": "work.lease_lost"}
    hb = asyncio.create_task(
        _heartbeat_loop(
            item.work_item_id,
            lease_token=item.lease_token,
            lease_fence=int(item.lease_fence or 0),
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            stop=stop,
            lease_lost=lease_lost,
            failure_code=failure_code,
        )
    )
    handler: asyncio.Task[Any] = asyncio.create_task(body())
    lost_waiter = asyncio.create_task(lease_lost.wait())
    try:
        done, _ = await asyncio.wait(
            {handler, lost_waiter}, return_when=asyncio.FIRST_COMPLETED
        )
        if handler in done:
            return await handler
        handler.cancel()
        await asyncio.gather(handler, return_exceptions=True)
        code = failure_code["code"]
        message = (
            "durable cancellation stopped the handler"
            if code == "work.cancelled"
            else "lease heartbeat failed; handler was cancelled"
        )
        raise WorkError(code, message)
    finally:
        stop.set()
        lost_waiter.cancel()
        await asyncio.gather(lost_waiter, return_exceptions=True)
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


def _native_chat_transcript(
    events: list[dict[str, Any]], *, error: dict[str, str] | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fold normalized provider events into the single durable chat result.

    Raw provider payloads and reasoning are deliberately not accepted here;
    the event store and transcript writer each apply their own public-field
    allowlists before persistence.
    """
    text = ""
    parts: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    timing: dict[str, Any] = {}
    for event in events:
        kind = event.get("type")
        if kind == "text-delta":
            text += str(event.get("delta") or "")
        elif kind == "text-replace":
            text = str(event.get("content") or "")
        elif kind == "tool-call":
            parts.append(
                {
                    "type": "tool-call",
                    "toolCallId": str(event.get("toolCallId") or ""),
                    "toolName": str(event.get("name") or ""),
                    "status": str(event.get("status") or "complete"),
                    "isError": event.get("status") == "error",
                }
            )
        elif kind == "source":
            parts.append(
                {
                    "type": "source",
                    "sourceType": "url",
                    "url": event.get("url"),
                    "title": event.get("title"),
                }
            )
        elif kind == "step":
            steps.append(
                {
                    key: event[key]
                    for key in (
                        "usage",
                        "modelId",
                        "provider",
                        "providerCostUsd",
                        "costSource",
                        "durationMs",
                        "outcome",
                        "attempt",
                        "requestId",
                    )
                    if key in event
                }
            )
        elif kind == "message-finish" and isinstance(event.get("timing"), dict):
            timing = dict(event["timing"])
        elif kind == "error":
            error = {
                "code": str(event.get("code") or "internal_error"),
                "message": str(event.get("message") or "The assistant turn failed."),
            }
    if text:
        parts.insert(0, {"type": "text", "text": text})
    if error:
        parts.append({"type": "error", **error})
    if not parts:
        parts.append({"type": "text", "text": ""})
    metadata: dict[str, Any] = {"steps": steps}
    if timing:
        metadata["timing"] = timing
    if error:
        metadata["error"] = error
    return parts, metadata


async def _handle_chat_turn(
    item: WorkItem,
    *,
    worker_id: str,
    lease_seconds: float,
) -> WorkItem:
    """Execute one native turn, committing each public event before replay."""
    from app.agentive.services.execution_runs import AgentRun, start_run
    from app.agentive.services.work_items import authorized_work_item_effect
    from app.services.chat_providers import ChatTurnContext, get_registry
    from app.services.chat_streaming import classify_turn_exception
    from app.services.chat_turn_events import (
        append_work_item_chat_event,
        replay_work_item_chat_events,
    )
    from app.services.chat_turn_transcript import (
        load_work_item_assistant_result,
        persist_work_item_assistant_result,
    )
    from app.services.chat_turn_worker import (
        ChatTerminalStatus,
        terminalize_chat_turn,
    )
    from app.services.chat_turn_worker_input import (
        load_claimed_chat_turn_input,
    )

    await _recheck_principal_workspace(item)
    ctx = work_execution.build_work_execution_context(
        work_item=item,
        logical_step_key=work_execution.logical_step_key_for(kind="chat_turn"),
    )

    async def _committed_events() -> list[dict[str, Any]]:
        """Read the complete committed event sequence for transcript folding."""
        events: list[dict[str, Any]] = []
        cursor = 0
        while True:
            page = await replay_work_item_chat_events(
                principal_id=ctx.principal_id,
                workspace_id=ctx.workspace_id,
                thread_id=ctx.thread_id,
                work_item_id=ctx.work_item_id,
                after_sequence=cursor,
                limit=500,
            )
            if page.gap:
                raise WorkError(
                    "work.event_sequence_gap",
                    "committed chat events contain a sequence gap",
                )
            events.extend(
                {key: value for key, value in event.items() if key != "sequence"}
                for event in page.events
            )
            if not page.has_more:
                return events
            if page.next_after_sequence <= cursor:
                raise WorkError(
                    "work.event_sequence_gap",
                    "committed chat event cursor did not advance",
                )
            cursor = page.next_after_sequence

    async def _terminalize_pre_stream_failure(
        failure: dict[str, str], *, status: ChatTerminalStatus = "failed"
    ) -> WorkItem:
        await append_work_item_chat_event(
            context=ctx,
            event_key=f"{ctx.run_id}:terminal-error",
            event={"type": "error", **failure},
        )
        parts, metadata = _native_chat_transcript(
            await _committed_events(), error=failure
        )
        await persist_work_item_assistant_result(
            context=ctx, parts=parts, provider_metadata=metadata
        )
        return await terminalize_chat_turn(
            ctx,
            status=status,
            failure=failure,
            result_fingerprint=ctx.effect_key,
        )

    async def _reconcile_committed_result() -> WorkItem | None:
        """Finish from terminal output already committed by an earlier attempt."""
        events = await _committed_events()
        error_event = next(
            (event for event in events if event.get("type") == "error"), None
        )
        has_terminal_event = error_event is not None or any(
            event.get("type") == "message-finish" for event in events
        )
        existing_result = await load_work_item_assistant_result(context=ctx)

        if not has_terminal_event and existing_result is None:
            return None

        if has_terminal_event:
            failure = (
                {
                    "code": str(error_event.get("code") or "internal_error"),
                    "message": str(
                        error_event.get("message") or "The assistant turn failed."
                    ),
                }
                if error_event is not None
                else None
            )
            status: ChatTerminalStatus = (
                "cancelled"
                if failure and failure["code"] == "work.cancelled"
                else "failed" if failure else "succeeded"
            )
        else:
            # A deterministic transcript without a committed terminal event
            # is evidence of an interrupted finalization, but not proof that
            # provider execution ended cleanly. Fail closed instead of issuing
            # another model request or changing the stable assistant message.
            failure = {
                "code": "work.chat_result_unreconciled",
                "message": (
                    "The assistant result was saved without a terminal event; "
                    "the turn needs outcome reconciliation."
                ),
            }
            status = "failed"
            await append_work_item_chat_event(
                context=ctx,
                event_key="work-item:reconciliation-error",
                event={"type": "error", **failure},
            )
            return await terminalize_chat_turn(
                ctx,
                status=status,
                failure=failure,
                result_fingerprint=ctx.effect_key,
            )

        parts, metadata = _native_chat_transcript(events, error=failure)
        await persist_work_item_assistant_result(
            context=ctx, parts=parts, provider_metadata=metadata
        )
        return await terminalize_chat_turn(
            ctx,
            status=status,
            failure=failure,
            result_fingerprint=ctx.effect_key,
        )

    try:
        worker_input = await load_claimed_chat_turn_input(item)
    except WorkError as exc:
        if exc.code in {"work.lease_lost", "work.deadline_exceeded"}:
            raise
        if exc.code == "work.cancelled":
            return await _terminalize_pre_stream_failure(
                {"code": exc.code, "message": exc.message}, status="cancelled"
            )
        return await _terminalize_pre_stream_failure(
            {"code": exc.code, "message": exc.message}
        )
    except Exception as exc:  # noqa: BLE001
        code, message = classify_turn_exception(exc)
        return await _terminalize_pre_stream_failure({"code": code, "message": message})

    reconciled = await _reconcile_committed_result()
    if reconciled is not None:
        return reconciled

    try:
        provider = get_registry().get("integral_native")
        if provider is None or not provider.is_available():
            return await _terminalize_pre_stream_failure(
                {
                    "code": "provider_unavailable",
                    "message": "Integral AI provider is unavailable.",
                }
            )

        # Reuse only an exactly scoped live run. Any terminal prior run needs
        # recovery reconciliation before a new model request may be started.
        existing = await AgentRun.find_one({"run_id": ctx.run_id})
        if existing is not None:
            if (
                existing.work_item_id != item.work_item_id
                or existing.thread_id != ctx.thread_id
                or existing.user_id != ctx.principal_id
                or existing.workspace_id != ctx.workspace_id
                or existing.provider_id != "integral_native"
                or existing.status != "running"
            ):
                raise WorkError(
                    "work.non_replayable_effect",
                    "native chat run requires outcome reconciliation",
                )
        else:
            async with authorized_work_item_effect(ctx):
                await start_run(
                    thread_id=ctx.thread_id,
                    user_id=ctx.principal_id,
                    workspace_id=ctx.workspace_id,
                    provider_id="integral_native",
                    origin="http",
                    run_id=ctx.run_id,
                    work_item_id=ctx.work_item_id,
                    deadline_at=ctx.deadline_at or "",
                    # WP-09 may rebuild an interrupted pre-dispatch turn from
                    # this accepted, scoped message ID. Never store its text.
                    metadata={
                        "work_item_id": ctx.work_item_id,
                        "chat_message_id": worker_input.message.id,
                    },
                )

        execution = worker_input.execution_context
        extra_data = dict(execution.extra_data)
        extra_data.update(
            {
                "run_id": ctx.run_id,
                "work_execution_context": ctx.model_dump(mode="json"),
            }
        )
        turn = ChatTurnContext(
            user_id=ctx.principal_id,
            user_email=worker_input.user_email,
            text=worker_input.text,
            thread_id=ctx.thread_id,
            session_id=worker_input.thread.provider_session_id,
            system_context=execution.system_context or None,
            focused_track_id=execution.focused_track_id,
            focused_space_id=execution.focused_space_id,
            focused_view_id=execution.focused_view_id,
            workspace_id=ctx.workspace_id,
            extra_data=extra_data,
        )
    except WorkError as exc:
        if exc.code in {"work.lease_lost", "work.deadline_exceeded"}:
            raise
        if exc.code == "work.cancelled":
            return await _terminalize_pre_stream_failure(
                {"code": exc.code, "message": exc.message}, status="cancelled"
            )
        return await _terminalize_pre_stream_failure(
            {"code": exc.code, "message": exc.message}
        )
    except Exception as exc:  # noqa: BLE001
        code, message = classify_turn_exception(exc)
        return await _terminalize_pre_stream_failure({"code": code, "message": message})
    event_ordinal = 0
    saw_message_finish = False

    async def _body() -> None:
        nonlocal event_ordinal, saw_message_finish
        await work_execution.assert_effect_boundary_allowed(ctx)
        async for event in provider.stream_turn(turn):
            await work_execution.assert_effect_boundary_allowed(ctx)
            # Provider session identifiers are adapter state, not public
            # events. Reasoning is intentionally excluded from chat replay.
            event_type = str(event.get("type") or "")
            if event_type in {"_meta", "reasoning-delta"}:
                continue
            await append_work_item_chat_event(
                context=ctx,
                event_key=f"{ctx.run_id}:{event_ordinal}",
                event=event,
            )
            event_ordinal += 1
            if event_type == "message-finish":
                saw_message_finish = True

    failure: dict[str, str] | None = None
    status: ChatTerminalStatus = "succeeded"
    try:
        await _with_supervised_heartbeat(
            item, worker_id=worker_id, lease_seconds=lease_seconds, body=_body
        )
    except WorkError as exc:
        if exc.code in {"work.lease_lost", "work.deadline_exceeded"}:
            raise
        if exc.code == "work.cancelled":
            status = "cancelled"
            failure = {"code": exc.code, "message": exc.message}
        else:
            status = "failed"
            failure = {"code": exc.code, "message": exc.message}
    except Exception as exc:  # noqa: BLE001
        code, message = classify_turn_exception(exc)
        status = "failed"
        failure = {"code": code, "message": message}

    events = await _committed_events()
    if status == "succeeded" and any(event.get("type") == "error" for event in events):
        status = "failed"
        error = next(event for event in events if event.get("type") == "error")
        failure = {
            "code": str(error.get("code") or "internal_error"),
            "message": str(error.get("message") or "The assistant turn failed."),
        }
    elif status == "succeeded" and not saw_message_finish:
        # A provider stream that ends without its explicit completion event is
        # not proof of a completed turn. Persist a terminal error so a retry
        # reconciles this outcome instead of issuing another paid request.
        status = "failed"
        failure = {
            "code": "work.chat_stream_incomplete",
            "message": (
                "The assistant stream ended before the turn was complete. "
                "Start a new message to try again."
            ),
        }

    if failure:
        error_event = {"type": "error", **failure}
        # Stable failure key lets recovery attach to the existing durable
        # stream without duplicating a public terminal error.
        already_persisted = any(
            event.get("type") == "error"
            and event.get("code") == failure["code"]
            and event.get("message") == failure["message"]
            for event in events
        )
        if not already_persisted:
            persisted = await append_work_item_chat_event(
                context=ctx,
                event_key=f"{ctx.run_id}:terminal-error",
                event=error_event,
            )
            events.append(
                {key: value for key, value in persisted.items() if key != "sequence"}
            )
    parts, metadata = _native_chat_transcript(events, error=failure)
    await persist_work_item_assistant_result(
        context=ctx, parts=parts, provider_metadata=metadata
    )
    return await terminalize_chat_turn(
        ctx,
        status=status,
        failure=failure,
        result_fingerprint=ctx.effect_key,
    )


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
    except (AppUninstallBlockedError, AppDependencyError) as exc:
        # Permanent lifecycle blockers must terminalize — a bare raise leaves
        # status=running and recovery reclaims forever (CRM-with-Sales case).
        code = getattr(exc, "error_code", None) or "app_lifecycle_blocked"
        message = getattr(exc, "message", None) or str(exc)
        return await work_items.transition_leased(
            item.work_item_id,
            lease_token=item.lease_token,
            lease_fence=int(item.lease_fence or 0),
            expected_status="running",
            target="failed",
            fields={
                "failure": WorkFailure(
                    class_="permanent",
                    code=str(code),
                    message=str(message),
                    retryable=False,
                ).model_dump(by_alias=True)
            },
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
