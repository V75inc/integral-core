"""Bounded native inference for an already admitted, reviewed mandate."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from functools import partial
from typing import Any

from app.agentive.harness.contracts import HarnessExecutionScope, ResolvedModelRoute
from app.schemas.agentive.work import WorkError, WorkExecutionContext
from app.schemas.agentive.work_mandate import WorkMandateRevision
from app.services.host_hooks import record_usage_event


async def run_reviewed_mandate_inference(
    context: WorkExecutionContext,
    revision: WorkMandateRevision,
    *,
    route: ResolvedModelRoute,
    resolve_route: Callable[[], Awaitable[ResolvedModelRoute]],
    instructions: str,
    prompt: dict[str, Any],
    capability_version: str,
) -> str:
    """Run Pydantic AI with the shared physical dispatch and receipt ledger."""
    from app.agentive.harness.litellm_model import build_litellm_sdk_model
    from app.agentive.harness.model_observations import (
        persist_model_request_observation,
    )
    from app.agentive.harness.pydantic_ai_compat import Agent, UsageLimits
    from app.agentive.services.work_model_admission import WorkModelAdmission

    async def authority() -> None:
        if await resolve_route() != route:
            raise WorkError("work.model_route_stale")

    admission = WorkModelAdmission(
        context=context,
        assert_authority=authority,
        observer=partial(
            persist_model_request_observation, work_execution_context=context
        ),
    )

    async def observe(observation):
        await admission.observe(observation)
        if observation.usage is not None:
            await record_usage_event(
                workspace_id=context.workspace_id,
                user_id=context.principal_id,
                run_id=context.run_id,
                idempotency_key=observation.request_id,
                source="platform" if route.credential_source == "platform" else "byok",
                input_tokens=observation.usage.input_tokens,
                output_tokens=observation.usage.output_tokens,
                model_id=route.model,
            )

    scope = HarnessExecutionScope(
        tenant_id=context.workspace_id,
        workspace_id=context.workspace_id,
        principal_id=context.principal_id,
        thread_id=context.thread_id,
        session_id=context.work_item_id,
        run_id=context.run_id,
        permission_revision=revision.review_digest(),
        capability_version=capability_version,
    )
    model = build_litellm_sdk_model(
        route=route,
        scope=scope,
        observer=observe,
        admission=admission,
        timeout_seconds=60,
        idle_timeout_seconds=30,
        max_attempts=1,
    )
    try:
        agent = Agent(
            model,
            retries=0,
            instructions=instructions,
        )
        remaining = (
            revision.limits.deadline_at - datetime.now(timezone.utc)
        ).total_seconds()
        async with asyncio.timeout(min(60, max(0, remaining))):
            result = await agent.run(
                json.dumps(prompt),
                model_settings={"max_tokens": 4096},
                usage_limits=UsageLimits(
                    request_limit=revision.limits.max_model_requests
                ),
            )
        return result.output.strip()
    finally:
        await model.client.close()
