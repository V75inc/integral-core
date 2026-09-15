"""POST /api/agentive/chat/message — typed Pydantic boundary, vendor-neutral dispatch.

AGT-03 first-class endpoint. D-04 typed bodies; D-09 AgentType Literal echoed in response.
"""

from typing import Optional, cast, get_args

from fastapi import Request
from jvspatial.api import endpoint

from app.agentive.connectors import ChatTurnContext
from app.agentive.connectors.registry import get_chat_connector
from app.agentive.services.uplink_registry import uplink_registry
from app.agentive.types import AgentType
from app.api.errors import (
    BadRequestError,
    MissingAuthenticationError,
    NotImplementedAPIError,
    ServiceUnavailableError,
)
from app.schemas.agentive.chat import ChatTurnResponse

# Runtime tuple of valid AgentType values for response coercion.
# AgentConnection.agent_type is a runtime str — we coerce to AgentType at the
# boundary if it's a known value, otherwise fall back to "custom" (D-09).
_VALID_AGENT_TYPES = frozenset(get_args(AgentType))


def _coerce_agent_type(raw: str) -> AgentType:
    """Coerce a runtime str into an AgentType Literal value.

    Unknown values (e.g., legacy seed data not yet migrated) map to "custom"
    so the response always carries a valid Literal value.
    """
    t = (raw or "").strip().lower()
    if t in _VALID_AGENT_TYPES:
        return cast(AgentType, t)
    return "custom"


SYSTEM_CHAT_PATH = "/agentive/chat/message"


@endpoint(
    "/agentive/chat/message",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def post_agentive_chat_message(
    request: Request,
    message: str = "",
    session_id: Optional[str] = None,
    focused_track_id: Optional[str] = None,
    focused_space_id: Optional[str] = None,
) -> ChatTurnResponse:
    """Send a chat message to the agentive layer and return the assistant turn."""
    from app.schemas.chat_entity_refs import EntityRef
    from app.services.chat_entity_refs import (
        entities_referenced_payload,
        focused_ids_from_resolved,
        resolve_entity_refs,
    )

    """Exchange one chat turn with the deployment agent.

    The authenticated user's **email** is the agent-facing user_id; client-supplied
    user identifiers are ignored. Optional ``session_id`` continues a thread.
    Vendor-neutral dispatch via get_chat_connector(conn.agent_type) — no jvagent
    type-narrowing (D-11).
    """
    # Pydantic-equivalent domain validation runs BEFORE service-availability
    # checks so callers get a deterministic 400 on malformed bodies regardless
    # of agentive harness state (parity with Phase 1 typed-boundary contract).
    # Plan 06-05 note: jvspatial's @endpoint flattens body fields but drops the
    # Pydantic ``min_length`` / ``max_length`` constraints from `FieldInfo`,
    # so the bounds are re-applied here as inline `BadRequestError` raises.
    if not message:
        raise BadRequestError(
            message="message is required and must be non-empty",
            details={"field": "message", "constraint": "min_length=1"},
        )
    if len(message) > 8000:
        raise BadRequestError(
            message="message exceeds 8000-character maximum",
            details={"field": "message", "constraint": "max_length=8000"},
        )
    if session_id is not None and len(session_id) > 128:
        raise BadRequestError(
            message="session_id exceeds 128-character maximum",
            details={"field": "session_id", "constraint": "max_length=128"},
        )

    # JWT or service-auth path: request.state.user is set by upstream auth middleware.
    user = getattr(request.state, "user", None)
    if not user:
        raise MissingAuthenticationError(message="Authentication required")
    email = (getattr(user, "email", None) or "").strip()
    if not email:
        raise BadRequestError(
            message="Authenticated user has no email; cannot start agent session"
        )

    user_id = str(getattr(user, "id", "") or "")
    entity_refs: Optional[list] = None
    try:
        raw_body = await request.json()
        if isinstance(raw_body, dict) and raw_body.get("entity_refs"):
            entity_refs = [
                EntityRef.model_validate(item) for item in raw_body["entity_refs"]
            ]
            if raw_body.get("focused_track_id") and not focused_track_id:
                focused_track_id = raw_body.get("focused_track_id")
            if raw_body.get("focused_space_id") and not focused_space_id:
                focused_space_id = raw_body.get("focused_space_id")
    except Exception:
        pass

    from app.services.request_scope import resolve_workspace_id_from_request

    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    ref_resolution = await resolve_entity_refs(
        message, entity_refs, user_id, workspace_id
    )
    if not focused_track_id or not focused_space_id:
        derived_track, derived_app = focused_ids_from_resolved(ref_resolution.resolved)
        focused_track_id = focused_track_id or derived_track
        focused_space_id = focused_space_id or derived_app

    agent_message = message
    if ref_resolution.context_preamble:
        agent_message = f"{ref_resolution.context_preamble}\n\n---\n\n{message}"

    # Phase 4 MEM-02 — ensure the user has a scratch Track before the
    # connector dispatches the turn. Provisioning is idempotent + cached
    # per-process; safe to call on every chat turn (cache-hit path is a
    # single in-memory dict read).
    try:
        from app.services.agent_scratch import provision_scratch_track

        await provision_scratch_track(user_id=str(getattr(user, "id", "") or ""))
    except Exception as exc:  # noqa: BLE001
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "scratch provisioning failed on chat turn (continuing): %s", exc
        )

    # Resolve the system AgentConfig.
    conn = await uplink_registry.get_system_agent()
    if not conn:
        raise ServiceUnavailableError(message="No deployment agent connected")

    # Vendor-neutral connector lookup (D-11) — no jvagent type-narrowing.
    try:
        connector = get_chat_connector(conn.agent_type)
    except ValueError as e:
        raise NotImplementedAPIError(message=str(e)) from e

    # Build context — `email` from authenticated principal, NEVER from body.
    extra: dict = {"workspace_id": workspace_id}
    if ref_resolution.resolved:
        extra["entities_referenced"] = entities_referenced_payload(
            ref_resolution.resolved
        )

    ctx = ChatTurnContext(
        email=email,
        message=agent_message,
        session_id=session_id,
        focused_track_id=focused_track_id,
        focused_space_id=focused_space_id,
        extra=extra,
    )

    # Single connector invocation. Connector returns ChatTurnResult — never raises.
    result = await connector.send_turn(ctx, preferences=conn.preferences)

    response_agent_type = _coerce_agent_type(conn.agent_type)

    # Normalize. Both branches return SAME response shape — vendor-neutral envelope.
    if result.error:
        return ChatTurnResponse(
            ok=False,
            error=result.error,
            session_id=result.session_id or ctx.session_id or "",
            agent_user_id=result.agent_user_id or email,
            agent_type=response_agent_type,
        )
    return ChatTurnResponse(
        ok=True,
        message=result.message,
        session_id=result.session_id,
        agent_user_id=result.agent_user_id or email,
        agent_type=response_agent_type,
    )
