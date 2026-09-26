"""``ChatBackendProvider`` adapter that fronts the jvagent harness.

The router only sees a single ``"jvagent"`` provider; this adapter
picks the right transport per-request based on what's wired:

* **Embedded**: :func:`jvagent.embed.bootstrap` runs at host startup
  (see ``app/main.py:_startup``). Streaming runs in-process via
  :func:`app.providers.jvagent_embed.stream_jvagent_embed_turn`. The
  active agent is picked by the user via the ``/agent`` surface and
  persisted as the jvspatial Agent node id on each ``ChatThread``.
* **HTTP** (single-agent): ``JVAGENT_BASE_URL`` +
  ``INTEGRAL_JVAGENT_AGENT_ID`` are set. Streaming opens an SSE
  connection to a separate jvagent process via
  :func:`app.providers.jvagent_streaming.stream_jvagent_turn`.
* **Neither**: provider reports ``is_available() == False`` so the
  ``GET /api/chat/providers`` response surfaces the harness as disabled
  and ``POST /threads/{id}/messages`` returns a clean 503.

The transport choice is private to this module — the router does not
need (and should not have) jvagent-specific knowledge.
"""

from __future__ import annotations

import inspect
import json
import logging
from typing import Any, AsyncIterator, Dict, List

from app.config import settings
from app.providers.jvagent_embed import stream_jvagent_embed_turn
from app.providers.jvagent_streaming import stream_jvagent_turn
from app.services.agent_trace import is_turn_end, log_turn_trace
from app.services.chat_providers.base import (
    AgentDescriptor,
    ChatBackendProvider,
    ChatProviderCapabilities,
    ChatTurnContext,
)
from app.services.chat_providers.loop_salvage import sanitize_loop_salvage

logger = logging.getLogger(__name__)

_JVAGENT_CHANNEL = "integral-ai-chat"

# Egress, discovery and planning; running only these means nothing was done.
_NON_ACTING_TOOLS = frozenset(
    {
        "reply",
        "respond",
        "use_skill",
        "find_tool",
        "load_tool",
        "clarify",
        "update_plan",
    }
)
# The harness must work in any language and with any model, so follow-through
# is never judged by matching words. A failed last step is read from the tool
# result; whether a reply that changed nothing left work hanging is judged by
# the model itself (``_reply_leaves_work_undone``).
# Worded as the user's own nudge: a system-voiced instruction gets
# acknowledged instead of acted on. It is never persisted as a user message,
# so it cannot count as approving a design.
# Quoting the reply back keeps the nudge tied to the work it names (a bare
# "finish that" got "nothing is pending") and anchors the reply language.
FOLLOW_THROUGH_UTTERANCE = (
    "You just told me:\n\n> {reply}\n\nPlease actually do that now. If "
    "something is really stopping you, tell me in one or two plain sentences "
    "what you need from me, with no technical detail. Answer in the same "
    "language as the message quoted above."
)
_SELF_CHECK_SYSTEM = (
    "You review one reply an assistant just gave. Answer with JSON only: "
    '{"unfinished": true} or {"unfinished": false}. unfinished is true only '
    "when the reply says the assistant is doing, is about to do, or will now "
    "do something that it has not actually done, or when it asks permission "
    "for the very thing the user asked for even though it is harmless, such "
    "as looking something up or drafting a plan for the user to review. It "
    "is false when the reply answers the question, reports finished work, "
    "waits for the user to choose, confirm, or supply something only they "
    "can, or merely offers optional extras the user did not ask for. The "
    "conversation may be in any language."
)


def _tool_call_failed(ev: Dict[str, Any]) -> bool:
    """A refused Integral tool returns normally with ``{"error": true}``."""
    if ev.get("status") == "error":
        return True
    result = ev.get("result")
    if isinstance(result, str) and result.lstrip().startswith("{"):
        try:
            result = json.loads(result)
        except ValueError:
            return False
    return isinstance(result, dict) and result.get("error") is True


async def _reply_leaves_work_undone(
    *, agent_id: str, workspace_id: str | None, utterance: str, reply: str
) -> bool:
    """Ask the agent's light model whether ``reply`` left promised work undone.

    Runs under the workspace's own model credentials. Any failure answers
    False: a missed nudge is better than a broken turn.
    """
    try:
        from jvagent.action.model.context import bind_model_gear
        from jvagent.core.agent import Agent

        from app.services.jvagent_harness import harness_model_override

        agent = await Agent.get(agent_id) if agent_id else None
        orchestrator = (
            await agent.get_action_by_type("OrchestratorInteractAction")
            if agent
            else None
        )
        if orchestrator is None:
            return False
        async with harness_model_override(workspace_id):
            model_action, model_id, *_ = await orchestrator._light_profile()
            if model_action is None:
                return False
            with bind_model_gear("light"):
                result = await model_action.query(
                    f"User message:\n{utterance[-2000:]}\n\n"
                    f"Assistant reply:\n{reply[-2000:]}",
                    system=_SELF_CHECK_SYSTEM,
                    calling_action_name="OrchestratorInteractAction",
                    model=model_id,
                    temperature=0,
                    max_tokens=20,
                )
        text = str(await result.get_response() or "")
        verdict = json.loads(text[text.find("{") : text.rfind("}") + 1])
        return verdict.get("unfinished") is True
    except Exception:  # noqa: BLE001 — the check must never fail a turn
        logger.debug("follow-through self-check failed", exc_info=True)
        return False


class _NullCache(dict):
    """A dict that refuses to retain entries — reads always miss.

    Module-level (not nested in the installer) so the ``isinstance`` guard in
    :meth:`JvagentProvider._disable_jvagent_overlay_caches` compares against a
    stable class and the install is genuinely idempotent.
    """

    def __setitem__(self, key: Any, value: Any) -> None:
        """Discard the write."""
        return

    def setdefault(self, key: Any, default: Any = None) -> Any:
        """Never retain; return the caller's default."""
        return default

    def update(self, *args: Any, **kwargs: Any) -> None:
        """Discard bulk writes."""
        return


async def _maybe_await(value: Any) -> Any:
    """jvagent.embed.list_agents may be sync or async — handle both."""
    if inspect.isawaitable(value):
        return await value
    return value


class JvagentProvider(ChatBackendProvider):
    """Default chat provider — backed by jvagent."""

    id: str = "jvagent"
    label: str = "jvagent"
    capabilities: ChatProviderCapabilities = ChatProviderCapabilities(
        reasoning=True,
        tools=True,
        attachments=True,
        vision=True,
        voice=False,
    )

    # ------------------------------------------------------------------
    # Transport detection
    # ------------------------------------------------------------------

    def _embed_configured(self) -> bool:
        """Return True when the embedded jvagent bundle is present."""
        try:
            from jvagent import embed  # noqa: F401
        except ImportError:
            return False
        from app.agentive.resident_root import resident_agent_root

        agent_root = resident_agent_root()
        return (agent_root / "app.yaml").exists()

    def _http_configured(self) -> bool:
        """Return True when legacy out-of-process mode has both env knobs set.

        Requires ``JVAGENT_BASE_URL`` and ``INTEGRAL_JVAGENT_AGENT_ID``.
        Read from the typed Settings object so env-loading rules stay
        consistent.
        """
        return bool(
            (settings.JVAGENT_BASE_URL or "").strip()
            and (settings.INTEGRAL_JVAGENT_AGENT_ID or "").strip()
        )

    def is_available(self) -> bool:
        """Return True when at least one transport (embed or HTTP) is wired."""
        return self._embed_configured() or self._http_configured()

    # ------------------------------------------------------------------
    # Turn execution
    # ------------------------------------------------------------------

    async def stream_turn(self, ctx: ChatTurnContext) -> AsyncIterator[Dict[str, Any]]:
        """Dispatch one turn to whichever transport is wired.

        Embed wins on overlap. The choice is decided per-request.
        """
        extra_data: Dict[str, Any] = {
            "thread_id": ctx.thread_id,
            "focused_track_id": ctx.focused_track_id,
            "focused_space_id": ctx.focused_space_id,
            "focused_view_id": ctx.focused_view_id,
            "workspace_id": ctx.workspace_id,
            "entities_referenced": (ctx.extra_data or {}).get("entities_referenced"),
        }
        # Caller-supplied extras override the canonical fields above
        # (last-write-wins) — gives forward room for harness-specific
        # context the router doesn't yet know about.
        if ctx.extra_data:
            extra_data.update(ctx.extra_data)

        # Agent the user picked for this thread. Carried in extra_data by
        # the dispatcher; populated from ChatThread.agent_id which holds
        # the jvspatial Agent node id (handed straight to
        # embed.interact_stream — no name → id lookup needed).
        selected_agent_id = ""
        if ctx.extra_data:
            selected_agent_id = (ctx.extra_data.get("agent_id") or "").strip()

        embed_configured = self._embed_configured()

        # Bind the active workspace scope onto the in-process ContextVar
        # that ``EmbeddedIntegralAction._stub_request`` reads. This makes
        # the agent's read/list tools see the same workspace the user has
        # selected in the UI rather than always falling back to the
        # Personal Workspace.
        #
        # Every token is set INSIDE the try below so the ``finally`` resets
        # them on any failure — including an overlay compose error, which
        # used to leave the ContextVars bound to this turn's workspace for
        # whatever ran next on the same task.
        _scope_token = None
        _track_focus_token = None
        _view_focus_token = None
        _page_context_token = None
        _thread_id_token = None
        overlay_failed = False
        try:
            if embed_configured:
                from app.services.agent_scope import (
                    current_chat_thread_id,
                    current_focused_track_id,
                    current_focused_view_id,
                    current_page_context,
                    current_scope_workspace_id,
                )

                _track_focus_token = current_focused_track_id.set(ctx.focused_track_id)
                _view_focus_token = current_focused_view_id.set(ctx.focused_view_id)
                if ctx.thread_id:
                    _thread_id_token = current_chat_thread_id.set(ctx.thread_id)
                page_ctx = (ctx.extra_data or {}).get("page_context")
                if isinstance(page_ctx, dict):
                    _page_context_token = current_page_context.set(page_ctx)
                if ctx.workspace_id:
                    _scope_token = current_scope_workspace_id.set(ctx.workspace_id)
                    from app.agentive.workspace_agent_profile import (
                        clear_turn_workspace_profile,
                        materialize_profile_for_turn,
                    )

                    try:
                        await materialize_profile_for_turn(
                            ctx.workspace_id,
                            user_id=ctx.user_id,
                            focused_app_id=ctx.focused_space_id,
                        )
                    except Exception:
                        # Degrade to the base skill set rather than killing
                        # the turn: the overlay is additive Tier-2 context.
                        logger.exception(
                            "workspace overlay compose failed for workspace=%s; "
                            "running turn without overlay",
                            ctx.workspace_id,
                        )
                        clear_turn_workspace_profile()
                        overlay_failed = True

                self._reset_jvagent_turn_caches(selected_agent_id)

            if embed_configured:
                utterance = ctx.text
                if not (utterance or "").strip():
                    trigger = (extra_data.get("trigger") or "").strip()
                    if trigger == "agent_workstream":
                        utterance = str(
                            extra_data.get("system_utterance") or "[agent workstream]"
                        )

                async def _embed_stream(text: str):
                    async for ev in stream_jvagent_embed_turn(
                        agent_id=selected_agent_id,
                        user_id=ctx.user_id,
                        text=text,
                        session_id=ctx.session_id,
                        channel=_JVAGENT_CHANNEL,
                        extra_data=extra_data,
                        start_time=ctx.start_time,
                        is_disconnected=ctx.is_disconnected,
                    ):
                        yield ev

                from app.services.jvagent_harness import stream_with_model_override

                stream = stream_with_model_override(
                    ctx.workspace_id, lambda: _embed_stream(utterance)
                )
            else:
                # HTTP (legacy) mode: out-of-process jvagent historically
                # matched users by email, so preserve that mapping here.
                # Decommissioned alongside this adapter once embed is
                # everyone's default.
                base_url = (settings.JVAGENT_BASE_URL or "").strip().rstrip("/")
                http_agent_id = (settings.INTEGRAL_JVAGENT_AGENT_ID or "").strip()
                stream = stream_jvagent_turn(
                    base_url=base_url,
                    agent_id=http_agent_id,
                    user_id=ctx.user_email,
                    text=ctx.text,
                    session_id=ctx.session_id,
                    channel=_JVAGENT_CHANNEL,
                    extra_data=extra_data,
                    start_time=ctx.start_time,
                )

            if overlay_failed:
                yield {
                    "type": "status",
                    "code": "workspace_overlay_unavailable",
                    "text": (
                        "Workspace skills could not be loaded for this turn; "
                        "continuing with the base skill set."
                    ),
                }

            from app.agentive.services.capability_broker import (
                infer_source_and_op_class,
            )

            last_step_failed = False
            changed_something = False
            reply = ""
            async for ev in stream:
                if (
                    ev.get("type") == "tool-call"
                    and ev.get("name") not in _NON_ACTING_TOOLS
                    and ev.get("status") != "running"
                ):
                    last_step_failed = _tool_call_failed(ev)
                    if not last_step_failed:
                        changed_something = changed_something or (
                            infer_source_and_op_class(str(ev.get("name") or ""))[1]
                            != "read"
                        )
                # One diagnosable line per turn: protocol and why, ticks and
                # gears, which guard fired, how it ended. jvagent already sends
                # this on the final envelope; without reading it a stalled turn
                # is only visible as a missing reply.
                if is_turn_end(ev):
                    log_turn_trace(ev, session_id=ctx.session_id or "")
                    reply = str(ev.get("content") or "")
                # The orchestrator's loop-guard bail pastes raw tool
                # observations into the reply; ours are JSON. See
                # loop_salvage for why this is rewritten rather than trimmed.
                yield sanitize_loop_salvage(ev)

            # One follow-up pass at most, so a model that cannot do the work
            # never loops; its second answer stands.
            follow_up = ""
            if reply.strip() and embed_configured:
                if last_step_failed:
                    follow_up = "failed_step"
                elif not changed_something and await _reply_leaves_work_undone(
                    agent_id=selected_agent_id,
                    workspace_id=ctx.workspace_id,
                    utterance=utterance,
                    reply=reply,
                ):
                    follow_up = "unfinished_reply"
            if follow_up and not (ctx.is_disconnected and await ctx.is_disconnected()):
                logger.info(
                    "jvagent turn left work undone; following through "
                    "session=%s reason=%s",
                    ctx.session_id or "",
                    follow_up,
                )
                yield {"type": "message-boundary"}
                self._reset_jvagent_turn_caches(selected_agent_id)
                async for ev in stream_with_model_override(
                    ctx.workspace_id,
                    lambda: _embed_stream(
                        FOLLOW_THROUGH_UTTERANCE.replace(
                            "{reply}", reply.strip().replace("\n", "\n> ")
                        )
                    ),
                ):
                    if is_turn_end(ev):
                        log_turn_trace(ev, session_id=ctx.session_id or "")
                    yield sanitize_loop_salvage(ev)
        finally:
            if ctx.workspace_id and embed_configured:
                from app.agentive.workspace_agent_profile import (
                    clear_turn_workspace_profile,
                )

                clear_turn_workspace_profile()
            if _scope_token is not None:
                from app.services.agent_scope import current_scope_workspace_id

                current_scope_workspace_id.reset(_scope_token)
            if _track_focus_token is not None:
                from app.services.agent_scope import current_focused_track_id

                current_focused_track_id.reset(_track_focus_token)
            if _view_focus_token is not None:
                from app.services.agent_scope import current_focused_view_id

                current_focused_view_id.reset(_view_focus_token)
            if _page_context_token is not None:
                from app.services.agent_scope import current_page_context

                current_page_context.reset(_page_context_token)
            if _thread_id_token is not None:
                from app.services.agent_scope import current_chat_thread_id

                current_chat_thread_id.reset(_thread_id_token)

    @staticmethod
    def _disable_jvagent_overlay_caches() -> bool:
        """Neutralize jvagent's two process-wide caches so they cannot cross-serve.

        Clearing the caches at the top of a turn is NOT sufficient once two
        turns overlap, which is the normal case on an async server: turn A
        clears, turn B clears, A discovers and writes the cache under A's
        overlay, then B reads a hit and gets A's workspace skills. Both caches
        are keyed on process-global values (app root / agent id) with no
        workspace or user component, so any stored entry is cross-tenant by
        construction.

        Because the previous stopgap already cleared both caches every turn,
        their steady-state hit rate across workspaces was ~0 — so replacing the
        backing dicts with ones that never store costs about the same and is
        race-free. Reads always miss, and each discovery re-merges against the
        current turn's ContextVar overlay.

        Idempotent; returns True when both caches are neutralized. The proper
        fix is still upstream (a host-supplied token in the cache key, or host
        docs excluded from the cached half) — see TODO(upstream jvagent) below.
        """
        ok = True
        try:
            from jvagent.action.orchestrator import skills as _jv_skills

            if not isinstance(_jv_skills._SKILL_DISCOVERY_CACHE, _NullCache):
                _jv_skills._SKILL_DISCOVERY_CACHE = _NullCache()
        except Exception:  # noqa: BLE001 — cache hygiene must not fail a turn
            logger.debug("could not neutralize skill discovery cache", exc_info=True)
            ok = False
        try:
            from jvagent.action.orchestrator import catalog as _jv_catalog

            if not isinstance(_jv_catalog._TOOL_SURFACE_CACHE, _NullCache):
                _jv_catalog._TOOL_SURFACE_CACHE = _NullCache()
        except Exception:  # noqa: BLE001
            logger.debug("could not neutralize tool surface cache", exc_info=True)
            ok = False
        return ok

    @staticmethod
    def _reset_jvagent_turn_caches(agent_id: str) -> None:
        """Drop jvagent's process-wide skill + tool surface caches for this turn.

        STOPGAP pending an upstream jvagent fix. ``discover_skill_docs``
        (``jvagent/action/orchestrator/skills.py``) caches the MERGED list —
        filesystem docs plus host-provider docs — keyed only on app root /
        agent / skills-tree mtime. Integral's host provider
        (``app/agentive/skill_bundle_provider.py``) answers from a
        per-(workspace, user) ContextVar, so the first turn's overlay was
        served to every later workspace and user: a cross-tenant prompt
        leak, and Tier-1 overlays never refreshed. Clearing before each turn
        forces re-merge against the overlay materialized above. The proper
        fix is upstream: exclude host-provider docs from the cache key (or
        cache only the filesystem half and merge host docs per call).

        TODO(upstream jvagent): ``_TOOL_SURFACE_CACHE[agent.id]``
        (``orchestrator_interact_action.py`` / ``catalog.py``) has the same
        shape — the first workspace's ``get_tools()`` result (which appends
        workspace bundle tools in ``EmbeddedIntegralAction``) is reused for
        every workspace. jvagent exposes ``invalidate_tool_surface_cache``
        so it is invalidated here too; that costs a full tool-surface
        re-assembly per turn. Upstream needs a host-supplied surface token
        in the config hash (or host tools excluded from the cache) so the
        cache can stay warm across turns of the same workspace.
        """
        # Preferred: make the caches incapable of retaining a cross-tenant
        # entry at all. Clearing alone loses the race between overlapping
        # turns (see _disable_jvagent_overlay_caches).
        if JvagentProvider._disable_jvagent_overlay_caches():
            return

        try:
            from jvagent.action.orchestrator.skills import (
                clear_skill_discovery_cache,
            )

            clear_skill_discovery_cache()
        except Exception:  # noqa: BLE001 — cache hygiene must not fail a turn
            logger.debug("clear_skill_discovery_cache unavailable", exc_info=True)
        try:
            from jvagent.action.orchestrator.catalog import (
                invalidate_tool_surface_cache,
            )

            invalidate_tool_surface_cache(agent_id or None)
        except Exception:  # noqa: BLE001
            logger.debug("invalidate_tool_surface_cache unavailable", exc_info=True)

    # ------------------------------------------------------------------
    # Agent catalog + conversation lifecycle (embed mode only)
    # ------------------------------------------------------------------
    #
    # Embed mode owns the jvagent runtime in-process so we can drive
    # discovery + memory operations directly via jvagent.embed.

    async def list_agents(self) -> List[AgentDescriptor]:
        """Discover agents from the jvagent embed runtime.

        Maps jvagent's ``list_agents()`` shape
        ``{id, namespace, name, alias, enabled, description}`` to the
        AgentDescriptor surface the API and frontend expect:
        ``id`` carries the jvspatial Agent node id (handed straight to
        ``embed.interact_stream`` at dispatch), ``name`` carries the
        agent's alias (display label).

        HTTP transport is single-agent only — returns an empty list so
        the switcher hides itself and the surface stays usable.
        """
        if not self._embed_configured():
            return []
        try:
            from jvagent import embed
        except ImportError:
            return []
        try:
            raw = await _maybe_await(embed.list_agents())
        except Exception:
            logger.exception("JvagentProvider.list_agents failed")
            return []

        result: List[AgentDescriptor] = []
        for entry in raw or []:
            if not entry.get("enabled", True):
                continue
            # id   = jvspatial Agent node id — opaque, used directly by
            #        ``embed.interact_stream(agent_id=...)`` so no
            #        name→id lookup is needed at dispatch time.
            # name = agent alias — the friendly, stable label shown in
            #        the UI.
            alias = entry.get("alias") or ""
            node_id = entry.get("id") or ""
            result.append(
                {
                    "id": node_id or alias or "",
                    "name": alias or entry.get("name") or "Agent",
                    "description": entry.get("description") or "",
                }
            )
        return result

    async def delete_conversation(
        self, *, agent_id: str, user_id: str, session_id: str
    ) -> bool:
        """Cascade-delete one jvagent Conversation + its Interactions.

        ``agent_id`` is the jvspatial Agent node id stored on the host's
        ``ChatThread.agent_id``. ``session_id`` is the jvagent session
        captured on the thread (``provider_session_id``). Idempotent —
        returns False when the agent or session has already been removed.
        """
        if not self._embed_configured() or not agent_id:
            return False
        from jvagent import embed

        try:
            return await embed.delete_conversation(
                agent_id=agent_id, session_id=session_id
            )
        except Exception:
            logger.exception(
                "JvagentProvider.delete_conversation failed "
                "(agent_id=%s, user_id=%s, session_id=%s)",
                agent_id,
                user_id,
                session_id,
            )
            return False


# Single shared instance — adapter is stateless, no need to construct per
# request. Registered in app/main.py at startup via
# ``get_registry().register(jvagent_provider, default=True)``.
jvagent_provider: ChatBackendProvider = JvagentProvider()
