"""Route explicit spoken continuations through existing durable chat commands."""

import hashlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from jvspatial.core.context import get_default_context

from app.api.errors import InsufficientPermissionsError, ResourceConflictError
from app.config import settings
from app.schemas.agentive.chat_followups import ChatFollowUpRequest, FollowUpStatus
from app.schemas.agentive.steering import SteeringCorrectionRequest
from app.schemas.agentive.work import ChatTurnSubmissionReceipt
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


def _channel_session(scope: VoiceContinuationScope) -> str:
    """Phone channel session of a scope; empty for browser voice scopes."""
    return getattr(scope, "channel_session", "") or ""


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
    if not is_workspace_voice_thread(
        row, scope.principal, scope.workspace, _channel_session(scope)
    ):
        raise InsufficientPermissionsError(message="Conversation unavailable")
    work_item_id = row["context"].get("active_work_item_id")
    if not work_item_id:
        return None
    return VoiceFollowUpTarget(
        VoiceFollowUpFence(
            scope.principal,
            scope.workspace,
            scope.thread,
            work_item_id,
            _channel_session(scope),
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
            target.fence.channel_session,
        )
        != (
            scope.principal,
            scope.workspace,
            scope.thread,
            _channel_session(scope),
        )
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


@dataclass(frozen=True)
class SpokenFollowUpObservation:
    """A bounded same-call read, never additional workspace authority."""

    state: Literal["queued", "dismissed", "promoted", "unavailable"]
    receipt: ChatTurnSubmissionReceipt | None = None


async def observe_spoken_follow_up(
    scope: VoiceContinuationScope, client_request_id: str
) -> SpokenFollowUpObservation:
    """Read a server-held receipt against its canonical promoted worker identity."""
    from app.agentive.harness.turn_input import load_turn_input_capsule
    from app.agentive.services.work_items import work_item_object_id
    from app.agentive.work_models import WorkItem
    from app.models.edges import CONTAINS
    from app.models.nodes import ChatMessage, ChatThread
    from app.services.chat_followups import read_spoken_follow_up_disposition
    from app.services.chat_turn_submissions import _submission_identity

    unavailable = SpokenFollowUpObservation("unavailable")
    if scope.profile or not re.fullmatch(
        r"voice\.follow-up\.[a-f0-9]{64}", client_request_id
    ):
        return unavailable
    keys = {
        "principal_id": scope.principal,
        "workspace_id": scope.workspace,
        "thread_id": scope.thread,
    }
    await require_voice_workspace_role(scope.principal, scope.workspace)
    row = await get_default_context().database.get("node", scope.thread)
    if not is_workspace_voice_thread(
        row, scope.principal, scope.workspace, _channel_session(scope)
    ):
        raise InsufficientPermissionsError(message="Conversation unavailable")

    key, message_id = _submission_identity(
        **keys, client_request_id=client_request_id, from_follow_up_queue=True
    )
    object_id = work_item_object_id(
        kind="chat_turn",
        origin="interactive_chat",
        principal_id=scope.principal,
        workspace_id=scope.workspace,
        idempotency_key=key,
    )
    item = await WorkItem.get(object_id)
    if item is None:
        disposition = await read_spoken_follow_up_disposition(
            **keys, client_request_id=client_request_id
        )
        if disposition != "unavailable":
            return SpokenFollowUpObservation(disposition)
        # Promotion removes the queue head in the same transaction that
        # accepts the worker. Account for a commit between the two reads.
        item = await WorkItem.get(object_id)
    if item is None or (
        item.principal_id,
        item.workspace_id,
        item.thread_id,
        item.kind,
    ) != (scope.principal, scope.workspace, scope.thread, "chat_turn"):
        return unavailable

    payload = item.input_payload or {}
    if payload.get("accepted_message_id") != message_id:
        return unavailable
    capsule_id, digest = payload.get("capsule_id"), payload.get("capsule_digest")
    if not isinstance(capsule_id, str) or not isinstance(digest, str):
        return unavailable
    try:
        capsule = await load_turn_input_capsule(
            **keys,
            work_item_id=item.work_item_id,
            capsule_id=capsule_id,
            expected_digest=digest,
        )
    except (LookupError, ValueError):
        return unavailable
    if (
        capsule.execution_context.extra_data.get("voice_delegate") is not True
        or capsule.execution_context.host_control is not None
    ):
        return unavailable

    thread = await ChatThread.get(scope.thread)
    message = await ChatMessage.get(message_id)
    if (
        thread is None
        or message is None
        or message.thread_id != scope.thread
        or message.role != "user"
        or message.parent_id is not None
    ):
        return unavailable
    graph = await thread.get_context()
    if not await graph.find_edges_between(
        scope.thread, message_id, edge_class=CONTAINS
    ):
        return unavailable
    await require_voice_workspace_role(scope.principal, scope.workspace)
    row = await get_default_context().database.get("node", scope.thread)
    if not is_workspace_voice_thread(
        row, scope.principal, scope.workspace, _channel_session(scope)
    ):
        raise InsufficientPermissionsError(message="Conversation unavailable")
    return SpokenFollowUpObservation(
        "promoted",
        ChatTurnSubmissionReceipt(
            client_request_id=client_request_id,
            message_id=message_id,
            work_item_id=item.work_item_id,
            status=item.status,
        ),
    )
