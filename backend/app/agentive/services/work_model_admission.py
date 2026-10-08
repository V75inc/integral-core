"""Physical model admission for durable approved work, before SDK dispatch."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from pydantic import ValidationError

from app.agentive.harness.contracts import PhysicalModelRequest
from app.agentive.services.work_budgets import (
    assert_mandate_model_dispatch_current,
    mark_mandate_dispatch_intent,
    reserve_mandate_budget,
)
from app.agentive.services.work_execution import (
    assert_effect_boundary_allowed,
    effect_key,
)
from app.agentive.services.work_items import assert_work_item_execution_current
from app.agentive.services.work_mandates import _load_lineage_item
from app.agentive.services.work_model_receipts import settle_mandate_model_receipt
from app.agentive.services.work_prices import resolve_mandate_model_reservation_request
from app.schemas.agentive.model_dispatch import ModelDispatchInput, ModelPayloadBounds
from app.schemas.agentive.work import WorkError, WorkExecutionContext
from app.schemas.agentive.work_mandate import MandateModelRoute
from app.schemas.agentive.work_price import ModelPriceRequest
from app.services.host_hooks import get_model_bounds_resolver

_BOUNDS_TIMEOUT_SECONDS = 10.0


async def _is_mandate_work(work_item_id: str) -> bool:
    """Identify durable review lineage, never infer approval from a chat context."""
    seen = set()
    for _ in range(32):
        if work_item_id in seen:
            raise WorkError("work.mandate_lineage_invalid")
        seen.add(work_item_id)
        item = await _load_lineage_item(work_item_id)
        if item.origin == "mandate_review" or any(
            key in item.plan
            for key in (
                "mandate_revision",
                "mandate_approval_id",
                "mandate_root_work_item_id",
            )
        ):
            return True
        if not item.parent_work_item_id:
            return False
        work_item_id = item.parent_work_item_id
    raise WorkError("work.mandate_lineage_invalid")


async def _resolve_bounds(dispatch: ModelDispatchInput) -> ModelPriceRequest:
    resolver = get_model_bounds_resolver()
    if resolver is None:
        raise WorkError("work.model_bounds_unavailable")
    try:
        candidate = await asyncio.wait_for(
            resolver(dispatch), timeout=_BOUNDS_TIMEOUT_SECONDS
        )
        bounds = ModelPayloadBounds.model_validate(candidate.model_dump(mode="python"))
    except WorkError:
        raise
    except Exception as exc:
        raise WorkError("work.model_bounds_resolution_failed") from exc
    if bounds.input_fingerprint != dispatch.fingerprint():
        raise WorkError("work.model_bounds_invalid")
    if bounds.valid_until <= datetime.now(timezone.utc):
        raise WorkError("work.model_bounds_expired")
    try:
        return ModelPriceRequest(
            scope=dispatch.scope,
            route=MandateModelRoute.model_validate(dispatch.route.model_dump()),
            request_id=dispatch.request_id,
            input_fingerprint=dispatch.fingerprint(),
            input_tokens_upper=bounds.input_tokens_upper,
            output_tokens_upper=bounds.output_tokens_upper,
            bounds_ref=bounds.bounds_ref,
            bounds_valid_until=bounds.valid_until,
        )
    except ValidationError as exc:
        raise WorkError("work.model_bounds_invalid") from exc


class WorkModelAdmission:
    """One run's model boundary; durable ledger remains the replay authority.

    Every SDK request gets a deterministic logical ordinal and separate physical
    identity. Unknown outcomes keep their hold. Restart cannot reuse an uncertain
    slot; ordinary chat work retains lease fencing without requiring a mandate.
    """

    def __init__(
        self,
        *,
        context: WorkExecutionContext,
        assert_authority: Callable[[], Awaitable[None]],
        observer: Callable[[PhysicalModelRequest], Awaitable[None]],
    ) -> None:
        self._context = context
        self._assert_authority = assert_authority
        self._observer = observer
        self._ordinal = 0
        self._reservations: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def __call__(
        self, dispatch: ModelDispatchInput
    ) -> Callable[[], Awaitable[None]]:
        """Reserve and fence approved work before any physical SDK call."""
        async with self._lock:
            return await self._admit(dispatch)

    async def _admit(
        self, dispatch: ModelDispatchInput
    ) -> Callable[[], Awaitable[None]]:
        ctx = self._context
        if (
            dispatch.scope.tenant_id != ctx.workspace_id
            or dispatch.scope.principal_id != ctx.principal_id
            or dispatch.scope.workspace_id != ctx.workspace_id
            or dispatch.scope.thread_id != ctx.thread_id
            or dispatch.scope.run_id != ctx.run_id
        ):
            raise WorkError("work.mandate_scope_denied")
        if dispatch.request_id in self._reservations:
            raise WorkError("work.idempotency_conflict")
        await assert_work_item_execution_current(ctx)
        await assert_effect_boundary_allowed(ctx)
        await self._assert_authority()
        if not await _is_mandate_work(ctx.work_item_id):

            async def ordinary_ready() -> None:
                await self._assert_authority()
                await assert_effect_boundary_allowed(ctx)
                await assert_work_item_execution_current(ctx)

            return self._one_shot(ordinary_ready)
        price_request = await _resolve_bounds(dispatch)
        logical_step = f"provider:{self._ordinal}"
        operation_context = ctx.model_copy(
            update={
                "logical_step_key": logical_step,
                "effect_key": effect_key(
                    work_item_id=ctx.work_item_id, logical_step_key=logical_step
                ),
            }
        )
        reservation_request = await resolve_mandate_model_reservation_request(
            price_request, logical_effect_key=operation_context.effect_key
        )
        # Resolver awaits may outlive permissions, route generation or lease.
        # Recheck immediately before the ledger's fenced authority transaction.
        await self._assert_authority()
        await assert_effect_boundary_allowed(operation_context)
        reservation = await reserve_mandate_budget(
            work_item_id=ctx.work_item_id,
            principal_id=ctx.principal_id,
            workspace_id=ctx.workspace_id,
            thread_id=ctx.thread_id,
            request=reservation_request,
            execution_context=operation_context,
        )
        await self._assert_authority()
        await mark_mandate_dispatch_intent(
            reservation_id=reservation.reservation_id,
            execution_context=operation_context,
            request_fingerprint=reservation_request.fingerprint(),
            dispatch_ref=dispatch.request_id,
            model_scope=dispatch.scope,
            model_price_request=price_request,
        )
        self._reservations[dispatch.request_id] = reservation.reservation_id
        self._ordinal += 1

        async def mandate_ready() -> None:
            await self._assert_authority()
            await assert_effect_boundary_allowed(operation_context)
            await assert_mandate_model_dispatch_current(
                reservation_id=reservation.reservation_id,
                execution_context=operation_context,
                model_price_request=price_request,
            )
            # No await follows these clocks before transport enters the SDK.
            # Even transaction close may have outlived the retained evidence.
            now = datetime.now(timezone.utc)
            if price_request.bounds_valid_until <= now:
                raise WorkError("work.model_bounds_expired")
            if reservation_request.quote.valid_until <= now:
                raise WorkError("work.cost_quote_expired")

        return self._one_shot(mandate_ready)

    @staticmethod
    def _one_shot(
        check: Callable[[], Awaitable[None]]
    ) -> Callable[[], Awaitable[None]]:
        consumed = False

        async def ready() -> None:
            nonlocal consumed
            if consumed:
                raise WorkError("work.outcome_reconciliation_required")
            consumed = True
            await check()

        return ready

    async def observe(self, observation: PhysicalModelRequest) -> None:
        """Persist first, then settle only definitive provider-priced evidence."""
        await self._observer(observation)
        reservation_id = self._reservations.get(observation.request_id)
        if reservation_id is None or observation.outcome == "dispatch_intent":
            return
        try:
            await settle_mandate_model_receipt(
                reservation_id=reservation_id,
                principal_id=self._context.principal_id,
                workspace_id=self._context.workspace_id,
                thread_id=self._context.thread_id,
            )
        except WorkError as exc:
            if exc.code != "work.cost_unavailable":
                raise
            # Missing/calculated/uncertain cost is expected on some routes.
            # The durable hold remains for reconciliation and limits later work.
