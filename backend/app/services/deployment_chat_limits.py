"""Shared installation limits. An immutable revision records each admin change."""

from jvspatial.core.context import get_default_context

from app.api.errors import BadRequestError, ResourceConflictError
from app.config import settings
from app.models.credentials import DeploymentChatLimitRevision
from app.schemas.deployment_chat_limits import ChatLimitOverrides, ChatLimitsWrite
from app.utils.time import utc_now_iso


def host_limits() -> dict[str, int]:
    """Return the host-defined ceilings."""
    return {
        "request_limit": settings.INTEGRAL_NATIVE_TURN_REQUEST_LIMIT,
        "tool_call_limit": 32,
        "token_limit": settings.INTEGRAL_NATIVE_TURN_TOKEN_LIMIT,
        "run_seconds": settings.INTEGRAL_HARNESS_CHAT_TURN_TIMEOUT_SECONDS,
        "request_timeout": settings.INTEGRAL_NATIVE_MODEL_REQUEST_TIMEOUT_SECONDS,
        "idle_timeout": settings.INTEGRAL_NATIVE_MODEL_IDLE_TIMEOUT_SECONDS,
    }


async def read_limits() -> dict:
    """Read the current deployment limit revision."""
    # Monotonic fixed-width primary keys support a bounded newest-first read
    # across server and worker processes. Reads never create a default record.
    rows = await get_default_context().database.find(
        "object", {"entity": "DeploymentChatLimitRevision"}, sort=[("id", -1)], limit=1
    )
    record = rows[0]["context"] if rows else None
    overrides = ChatLimitOverrides.model_validate(
        record["overrides"] if record else {}
    ).model_dump(exclude_none=True)
    host = host_limits()
    effective = {k: min(v, overrides.get(k, v)) for k, v in host.items()}
    return {
        "revision": record["revision"] if record else 0,
        "whole_answer_protection": (record or {}).get("whole_answer_protection", False)
        is not False,
        "host": host,
        "overrides": overrides,
        "effective": effective,
    }


async def save_limits(actor_id: str, body: ChatLimitsWrite) -> dict:
    """Persist a validated administrator revision."""
    current = await read_limits()
    if current["revision"] != body.revision:
        raise ResourceConflictError(
            message="Deployment limits changed. Reload the current values before saving."
        )
    overrides = body.overrides.model_dump(exclude_none=True)
    for key, value in overrides.items():
        if value > current["host"][key]:
            raise BadRequestError(
                f"{key} exceeds the host maximum of {current['host'][key]}."
            )
    protection = body.whole_answer_protection
    if protection is None:
        protection = current["whole_answer_protection"]
    if (
        overrides == current["overrides"]
        and protection == current["whole_answer_protection"]
    ):
        return current
    revision = body.revision + 1
    _, created = await DeploymentChatLimitRevision.create_if_absent(
        id=f"o.DeploymentChatLimitRevision.{revision:020d}",
        revision=revision,
        overrides=overrides,
        whole_answer_protection=protection,
        actor_id=actor_id,
        updated_at=utc_now_iso(),
    )
    if not created:
        raise ResourceConflictError(
            message="Deployment limits changed. Reload the current values before saving."
        )
    return await read_limits()


def turn_budget(effective: dict[str, int]) -> dict[str, int]:
    """Translate effective deployment limits to native turn budgets."""
    return {
        "model_request_limit": effective["request_limit"],
        "tool_call_limit": effective["tool_call_limit"],
        "turn_token_limit": effective["token_limit"],
        "turn_timeout": effective["run_seconds"],
        "request_timeout": effective["request_timeout"],
        "idle_timeout": effective["idle_timeout"],
    }
