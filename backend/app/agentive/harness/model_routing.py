"""Host-owned light/heavy roles within Integral's single resident harness."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable

from app.config import settings
from app.services.model_credential_resolver import (
    _credential_funds_byok,
    _workspace_owner_credential,
)


@dataclass(frozen=True)
class TurnModelRoutes:
    heavy: Any
    light: Any
    mode: str = "single"
    revision: str = ""

    @property
    def budget_settings(self) -> dict[str, Any]:
        """Every configured run ceiling remains binding through escalation."""
        result: dict[str, Any] = {}
        for route in (self.heavy, self.light):
            for key in (
                "model_request_limit",
                "tool_call_limit",
                "turn_token_limit",
                "turn_timeout",
            ):
                value = getattr(route, "generation_settings", {}).get(key)
                if value is not None:
                    result[key] = min(result.get(key, value), value)
        return result


def deployment_settings(role: str, model: str) -> dict[str, Any]:
    """Parse typed operator settings without exposing submitted values in errors."""
    from app.services.model_settings import validate_model_settings

    raw = os.getenv(f"INTEGRAL_NATIVE_{role.upper()}_MODEL_SETTINGS", "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError()
        provider, _, name = model.partition("/")
        provider = {"ollama": "ollama_local", "ollama_chat": "ollama_local"}.get(
            provider, provider
        )
        return validate_model_settings(provider, name, value)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid deployment {role} model settings") from None


def deployment_mode() -> str:
    """Validate the operator's optional dual-model routing selection."""
    mode = os.getenv("INTEGRAL_NATIVE_ROUTING_MODE", "single").strip()
    if mode not in {"single", "light_heavy"}:
        raise ValueError("Invalid deployment model routing mode")
    if (
        mode == "light_heavy"
        and not os.getenv("INTEGRAL_NATIVE_LIGHT_MODEL", "").strip()
    ):
        raise ValueError("Automatic deployment routing requires a light model")
    return mode


async def effective_routing_summary(workspace_id: str) -> dict[str, Any]:
    """Describe roles without disclosing another owner's private profile IDs."""
    record = (
        await _workspace_owner_credential(workspace_id)
        if settings.INTEGRAL_AGENT_KEY_MODE != "platform_only"
        else None
    )
    if record and _credential_funds_byok(record):
        from app.models.credentials import UserModelProfile

        light = (
            await UserModelProfile.get(record.light_profile_id)
            if record.light_profile_id
            else None
        )
        return {
            "mode": record.routing_mode,
            "heavy_model": record.model,
            "heavy_generation_settings": record.generation_settings,
            "light_generation_settings": (
                light.generation_settings
                if light and light.user_id == record.user_id
                else {}
            ),
            "light_model": (
                light.model if light and light.user_id == record.user_id else ""
            ),
        }
    available = settings.INTEGRAL_AGENT_KEY_MODE != "byo_strict"
    heavy_model = os.getenv("INTEGRAL_NATIVE_MODEL", "") if available else ""
    light_model = os.getenv("INTEGRAL_NATIVE_LIGHT_MODEL", "") if available else ""
    mode = deployment_mode() if available else "single"
    return {
        "mode": mode,
        "heavy_model": heavy_model,
        "light_model": light_model if mode == "light_heavy" else "",
        "heavy_generation_settings": (
            deployment_settings("heavy", heavy_model) if heavy_model else {}
        ),
        "light_generation_settings": (
            deployment_settings("light", light_model)
            if light_model and mode == "light_heavy"
            else {}
        ),
    }


async def resolve_turn_routes(
    *,
    workspace_id: str,
    profile_id: str = "",
    resolver: Callable,
    heavy_only: bool = False,
) -> TurnModelRoutes:
    """Resolve both roles from one funding authority; never change global models."""
    heavy = await resolver(
        workspace_id=workspace_id,
        default_model=os.getenv("INTEGRAL_NATIVE_MODEL", "").strip(),
        **({"profile_id": profile_id} if profile_id else {}),
    )
    source = getattr(heavy, "credential_source", None)
    owner_record = (
        await _workspace_owner_credential(workspace_id)
        if source == "local" and settings.INTEGRAL_AGENT_KEY_MODE != "platform_only"
        else None
    )
    deployment = source == "platform" or (
        source == "local"
        and (
            settings.INTEGRAL_AGENT_KEY_MODE == "platform_only"
            or not owner_record
            or not _credential_funds_byok(owner_record)
        )
    )
    if deployment:
        heavy_settings = deployment_settings("heavy", heavy.model)
        heavy = (
            heavy.model_copy(update={"generation_settings": heavy_settings})
            if heavy_settings
            else heavy
        )
    if profile_id or heavy_only:
        return TurnModelRoutes(heavy, heavy)
    if deployment:
        mode = deployment_mode()
        light = heavy
        if mode == "light_heavy":
            from app.agentive.harness.model_route import resolve_deployment_model_route

            light = resolve_deployment_model_route(
                os.getenv("INTEGRAL_NATIVE_LIGHT_MODEL", "").strip()
            )
            light = light.model_copy(
                update={
                    "generation_settings": deployment_settings("light", light.model)
                }
            )
        return TurnModelRoutes(heavy, light, mode)
    if source in {"workspace_byok", "local"}:
        record = await _workspace_owner_credential(workspace_id)
        if record and record.routing_mode == "light_heavy":
            if not record.light_profile_id:
                raise ValueError("Automatic routing is missing its light model")
            light = await resolver(
                workspace_id=workspace_id,
                default_model="",
                profile_id=record.light_profile_id,
            )
            return TurnModelRoutes(heavy, light, "light_heavy", record.updated_at or "")
    return TurnModelRoutes(heavy, heavy)


def may_escalate_light_failure(
    exc: BaseException, plan: TurnModelRoutes | None
) -> bool:
    """Only known transient provider failures qualify; host denials never do."""
    from app.agentive.harness.model_errors import classified_model_error

    return bool(
        plan
        and plan.mode == "light_heavy"
        and classified_model_error(exc)
        in {
            "model_request_timeout",
            "model_endpoint_unreachable",
            "model_provider_unavailable",
        }
    )


async def bind_durable_routing(scope, context, plan: TurnModelRoutes, timing) -> None:
    """Fence a dual-model run's selection and budget across worker recovery."""
    import hashlib
    import time

    from jvspatial.core.context import get_default_context

    from app.agentive.harness.model_observations import (
        list_run_model_request_observations,
    )
    from app.agentive.harness.pydantic_ai_compat import UsageLimitExceeded
    from app.agentive.services.execution_runs import AgentRun
    from app.agentive.services.work_items import authorized_work_item_effect
    from app.schemas.agentive.work import WorkError

    if context is None:
        return
    if plan.mode != "light_heavy":
        prior = await AgentRun.find_one(
            {
                "run_id": scope.run_id,
                "user_id": scope.principal_id,
                "workspace_id": scope.workspace_id,
                "thread_id": scope.thread_id,
            }
        )
        if prior and (prior.metadata or {}).get("model_routing"):
            raise WorkError("work.model_route_stale")
        return
    # Route material is hashed, not retained. The actual request observations
    # remain encrypted and carry the immutable physical route attribution.
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "mode": plan.mode,
                "revision": plan.revision,
                "heavy": plan.heavy.model_dump(mode="json", exclude={"api_key"}),
                "light": plan.light.model_dump(mode="json", exclude={"api_key"}),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    async with authorized_work_item_effect(context):
        run = await AgentRun.find_one({"run_id": scope.run_id})
        if (
            run is None
            or run.user_id != scope.principal_id
            or run.workspace_id != scope.workspace_id
            or run.thread_id != scope.thread_id
            or run.status != "running"
        ):
            raise WorkError("work.policy_denied", "execution run mismatch")
        saved = (run.metadata or {}).get("model_routing")
        if saved is None:
            remaining = (
                (timing.deadline.when() - time.monotonic())
                if timing.deadline and timing.deadline.when() is not None
                else settings.INTEGRAL_HARNESS_CHAT_TURN_TIMEOUT_SECONDS
            )
            saved = {
                "fingerprint": fingerprint,
                "deadline_epoch": time.time() + remaining,
            }
            changed = await get_default_context().database.find_one_and_update(
                "object",
                {
                    "id": run.id,
                    "context.status": "running",
                    "context.metadata.model_routing": {"$exists": False},
                },
                {"$set": {"context.metadata.model_routing": saved}},
            )
            if changed is None:
                raise WorkError("work.model_route_stale")
            # Match the work kernel's field-only CAS cache protocol. Eviction
            # also invalidates the parent identity map when the txn commits.
            await get_default_context()._evict_from_cache(run.id)
        if not isinstance(saved, dict) or saved.get("fingerprint") != fingerprint:
            raise WorkError("work.model_route_stale")
        remaining = saved.get("deadline_epoch", 0) - time.time()
        if remaining <= 0:
            raise UsageLimitExceeded("The shared turn deadline is exhausted")
        if timing.deadline is not None:
            timing.deadline.reschedule(
                min(
                    timing.deadline.when() or float("inf"), time.monotonic() + remaining
                )
            )
        observations = await list_run_model_request_observations(
            run_id=scope.run_id,
            principal_id=scope.principal_id,
            workspace_id=scope.workspace_id,
            thread_id=scope.thread_id,
        )
        timing.physical_requests = len({item.request_id for item in observations})
        for item in observations:
            timing.observe_usage(item)
