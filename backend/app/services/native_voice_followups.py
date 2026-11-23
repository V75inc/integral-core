"""Route explicit spoken continuations through existing durable chat commands."""

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from jvspatial.core.context import get_default_context

from app.api.errors import InsufficientPermissionsError, ResourceConflictError
from app.config import settings
from app.schemas.agentive.chat_followups import ChatFollowUpRequest, FollowUpStatus
from app.schemas.agentive.steering import SteeringCorrectionRequest
from app.services.chat_followups import accept_chat_follow_up
from app.services.chat_steering import accept_steering_correction
from app.services.chat_voice_fence import (
    VoiceFollowUpFence,
    is_workspace_voice_thread,
    require_voice_workspace_role,
)


class VoiceContinuationScope(Protocol):
    principal: str
    workspace: str
    profile: str
    thread: str
    session: str


@dataclass(frozen=True)
class VoiceFollowUpTarget:
    """A server-authored target snapshot. Keep it unchanged on retries."""

    fence: VoiceFollowUpFence
    session: str


@dataclass(frozen=True)
class VoiceFollowUpResult:
    status: FollowUpStatus | Literal["steering_accepted"]
    client_request_id: str
    predecessor_work_item_id: str


async def resolve_voice_follow_up_target(
    scope: VoiceContinuationScope,
) -> VoiceFollowUpTarget | None:
    """Resolve the current exact target; committing a change fences it again."""
    if scope.profile:
        raise ResourceConflictError(
            message="Voice follow-ups and refinements are unavailable for selected profiles"
        )
    if not scope.thread:
        raise InsufficientPermissionsError(message="Conversation unavailable")
    await require_voice_workspace_role(scope.principal, scope.workspace)
    row = await get_default_context().database.get("node", scope.thread)
    if not is_workspace_voice_thread(row, scope.principal, scope.workspace):
        raise InsufficientPermissionsError(message="Conversation unavailable")
    work_item_id = row["context"].get("active_work_item_id")
    if not work_item_id:
        return None
    return VoiceFollowUpTarget(
        VoiceFollowUpFence(
            scope.principal, scope.workspace, scope.thread, work_item_id
        ),
        scope.session,
    )


async def route_voice_follow_up(
    scope: VoiceContinuationScope,
    call_id: str,
    text: str,
    *,
    target: VoiceFollowUpTarget,
    operation: Literal["queue", "steer"],
    check: Callable[[VoiceContinuationScope], Awaitable[object]] | None = None,
) -> VoiceFollowUpResult:
    """Accept one intentional input. Neither failure nor staleness re-targets it."""
    if not settings.INTEGRAL_NATIVE_DURABLE_CHAT_ENABLED:
        raise ResourceConflictError(message="Durable voice continuation is unavailable")
    if (
        scope.profile
        or target.session != scope.session
        or (
            target.fence.principal_id,
            target.fence.workspace_id,
            target.fence.thread_id,
        )
        != (scope.principal, scope.workspace, scope.thread)
    ):
        raise InsufficientPermissionsError(message="Conversation unavailable")
    if not isinstance(call_id, str) or not 0 < len(call_id) <= 128:
        raise ValueError("Invalid voice call identity")
    if not isinstance(text, str) or not 0 < len(text.strip()) <= 2000:
        raise ValueError("Invalid voice continuation")
    # The relay uses the provider call ID only for dedup, never client target
    # selection. A domain-separated key cannot collide across voice sessions.
    request_id = (
        "voice.follow-up."
        + hashlib.sha256(f"{scope.session}:{call_id}".encode()).hexdigest()
    )
    fence = target.fence
    if check is not None:
        await check(scope)
    if operation == "queue":
        status = await accept_chat_follow_up(
            principal_id=fence.principal_id,
            workspace_id=fence.workspace_id,
            thread_id=fence.thread_id,
            request=ChatFollowUpRequest(
                client_request_id=request_id,
                after_work_item_id=fence.work_item_id,
                text=text,
            ),
            voice_fence=fence,
        )
    elif operation == "steer":
        await accept_steering_correction(
            fence.work_item_id,
            fence.principal_id,
            SteeringCorrectionRequest(client_request_id=request_id, text=text),
            voice_fence=fence,
        )
        status = "steering_accepted"
    else:
        raise ValueError("Invalid voice continuation action")
    if check is not None:
        await check(scope)
    return VoiceFollowUpResult(status, request_id, fence.work_item_id)
