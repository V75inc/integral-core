"""Conversation context management for the agentive layer.

Handles CRUD for ConversationContext nodes that bridge
agent conversations to Integral's productivity entities.
"""

import logging
from typing import Optional

from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


async def get_or_create_conversation_context(
    user_id: str,
    agent_type: str,
    agent_conversation_id: str,
    **kwargs,
):
    """Get or create an Integral ConversationContext for an agent conversation."""
    from app.agentive.nodes import ConversationContext

    existing = await ConversationContext.find_one(
        agent_type=agent_type,
        agent_conversation_id=agent_conversation_id,
    )
    if existing:
        return existing

    now = utc_now_iso()
    ctx = await ConversationContext.create(
        agent_type=agent_type,
        agent_conversation_id=agent_conversation_id,
        user_id=user_id,
        scope=kwargs.get("scope", "personal"),
        persona=kwargs.get("persona", ""),
        workspace_id=kwargs.get("workspace_id"),
        agent_config_id=kwargs.get("agent_config_id"),
        parent_context_id=kwargs.get("parent_context_id"),
        created_at=now,
        updated_at=now,
    )
    # Phase 10.5 Plan 10.5-07 (I-GRAPH-01): wire CONTAINS edge so the
    # ConversationContext is reachable from the rooted subgraph.
    # Anchor selection:
    #   * agent_config_id resolves -> AgentConfig -CONTAINS-> ctx
    #     (the canonical parent — the conversation belongs to the agent).
    #   * else user resolves       -> User -CONTAINS-> ctx
    #     (fallback for agent-agnostic / pre-AgentConfig contexts).
    # Fail closed with rollback — never leave an orphan ConversationContext.
    try:
        from app.models.edges import CONTAINS

        parent = None
        agent_config_id = kwargs.get("agent_config_id")
        if agent_config_id:
            from app.agentive.nodes import AgentConfig

            parent = await AgentConfig.get(agent_config_id)
        if parent is None:
            from app.services.permissions import get_user_node

            parent = await get_user_node(user_id) if user_id else None
        if parent is None:
            raise RuntimeError(
                f"No graph parent for ConversationContext "
                f"(user={user_id}, agent_config={kwargs.get('agent_config_id')})"
            )
        await parent.connect(ctx, edge=CONTAINS, added_at=now)
    except Exception:
        logger.exception(
            "get_or_create_conversation_context: CONTAINS wire failed for ctx=%s "
            "user=%s agent_config=%s; rolling back orphaned context",
            ctx.id,
            user_id,
            kwargs.get("agent_config_id"),
        )
        try:
            await ctx.delete()
        except Exception:
            logger.exception(
                "get_or_create_conversation_context: rollback delete failed "
                "for ctx=%s",
                ctx.id,
            )
        raise
    return ctx


async def update_conversation_context(
    context_id: str,
    focused_track_id: Optional[str] = None,
    focused_space_id: Optional[str] = None,
    entities_referenced: Optional[list] = None,
    user_patterns: Optional[dict] = None,
):
    """Update a ConversationContext's focus and references."""
    from app.agentive.nodes import ConversationContext

    ctx = await ConversationContext.get(context_id)
    if not ctx:
        return None

    if focused_track_id is not None:
        ctx.focused_track_id = focused_track_id
    if focused_space_id is not None:
        ctx.focused_space_id = focused_space_id
    if entities_referenced is not None:
        ctx.entities_referenced = entities_referenced
    if user_patterns is not None:
        ctx.user_patterns = user_patterns

    ctx.updated_at = utc_now_iso()
    await ctx.save()
    return ctx


async def set_focus_for_dispatch(
    *,
    user_id: str,
    context_id: Optional[str] = None,
    focused_track_id: Optional[str] = None,
    focused_space_id: Optional[str] = None,
) -> dict:
    """Set the active focus on a ConversationContext (M2a direct-dispatch shape).

    This is the ``direct_ref`` target for the ``integral_set_focus`` tool. The
    tool is ``propose`` in the manifest's op-class taxonomy but writes NO
    substrate — ``focused_track_id`` / ``focused_space_id`` are EPHEMERAL agent
    conversation state, so it runs immediately (no StagedChange / bless; PC-8
    does not apply).

    Signature contract: :func:`app.agentive.tooling.dispatch._dispatch_direct`
    calls every direct-ref fn as ``await fn(user_id=<principal_id>, **kwargs)``.
    ``user_id`` is the dispatch principal (PC-1 — identity is never an arg).
    Ownership is enforced here: the targeted ConversationContext MUST belong to
    the acting principal, else we fail closed (a context_id arg can never let a
    caller mutate another user's session state).

    Fail-closed: ``set_focus`` mutates a SPECIFIC ConversationContext keyed by
    ``context_id``. The M2a external-MCP dispatch contract carries only
    ``principal_id`` + workspace ``scope`` — it does NOT carry a session /
    conversation id. So when no ``context_id`` reaches this fn (the external
    surface), we return a clean error envelope rather than guessing a context.
    The RESIDENT orchestration supplies the live ``context_id`` (it owns the
    ConversationContext for the turn); that is the path on which this tool is
    actually exercised today.
    """
    if not context_id:
        return {
            "error": "context_required",
            "detail": (
                "set_focus needs a conversation context_id; the resident "
                "supplies it. The external dispatch contract carries no "
                "session/context id."
            ),
        }
    if focused_track_id is None and focused_space_id is None:
        return {
            "error": "no_focus",
            "detail": "supply focused_track_id and/or focused_space_id",
        }

    from app.agentive.nodes import ConversationContext

    ctx = await ConversationContext.get(context_id)
    if not ctx:
        return {"error": "not_found", "detail": "Conversation context not found"}
    # Ownership gate (PC-1): a context_id arg must not let one principal mutate
    # another's conversation state. The dispatch user_id is authoritative.
    #
    # Fail CLOSED on an unowned context. ``ConversationContext.user_id`` defaults
    # to ``""``; the ONLY legitimate creation path
    # (``get_or_create_conversation_context``, behind the auth-gated
    # ``create_conversation_context`` endpoint) ALWAYS sets a non-empty owner, so
    # an empty/missing ``owner_id`` here is an anomaly — never a valid resident
    # session. The earlier guard short-circuited on a falsy ``owner_id``, letting
    # ANY caller mutate such a context. Require an explicit owner match: an empty
    # owner (or a foreign owner) is rejected.
    owner_id = getattr(ctx, "user_id", None) or ""
    if owner_id != user_id:
        return {
            "error": "forbidden",
            "detail": "Conversation context does not belong to the caller",
        }

    ctx = await update_conversation_context(
        context_id=context_id,
        focused_track_id=focused_track_id,
        focused_space_id=focused_space_id,
    )
    return {
        "context_id": ctx.id if ctx else context_id,
        "focused_track_id": (
            getattr(ctx, "focused_track_id", None) if ctx else focused_track_id
        ),
        "focused_space_id": (
            getattr(ctx, "focused_space_id", None) if ctx else focused_space_id
        ),
        "message": "Focus updated",
    }
