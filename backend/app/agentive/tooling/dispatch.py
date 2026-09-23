"""Dispatch a manifest tool by name under a principal + bound scope.

:func:`dispatch_tool` is the single entry point downstream surfaces
(``IntegralAction.get_tools()`` forwarders, the MCP adapter) call to run a tool.
Central tools join the manifest :class:`~app.agentive.tooling.manifest.ToolSpec`
(the contract) with the :class:`~app.agentive.tooling.bindings.ToolBinding`
(the wiring). When a name is not central, dispatch falls back to the trusted
bundle-local tool registry for the currently bound workspace. For ``op_class == "read"`` tools it dispatches via one of two seams:

* **Service-backed** (``binding.service_ref`` set) — call the service FUNCTION
  directly as ``await fn(user_id=<principal_id>, **service_param_map(args))``,
  with the workspace scope bound via the ``current_scope_workspace_id``
  ContextVar around the call (reset in ``finally`` so it can't leak).
* **Route-backed** (``binding.handler_ref`` set) — map the caller's args to
  handler kwargs / JSON body / query params and invoke the route in-process
  under the resolved principal + bound scope via
  :func:`~app.agentive.tooling.invoke.invoke_route_in_process`.

Security posture (PC-1 / PC-2 / PC-6):

* Identity and scope come ONLY from ``principal_id`` / ``scope`` here. The
  ``param_map`` / ``body_map`` / ``query_map`` / ``service_param_map`` map data
  args and can NEVER inject identity (the service ``user_id`` is the dispatch
  ``principal_id``) or a scope-widening value (scope is the bound ``scope``).
* Fail-closed: any handler/service exception (``JVSpatialAPIException``
  permission denials, lazy-import errors, polymorphic ``ValueError``) becomes an
  error :class:`ToolResult`, never a partial or leaky success.
* The ``user_not_found`` envelope returned by ``invoke_route_in_process`` (a
  dict with ``error=True``) is normalized into an error ToolResult.

For ``op_class == "propose"`` tools, :func:`_dispatch_propose` runs the
binding's ``stager`` (sync or async) to map args -> ``create_staged_change``
kwargs and mints a *pending* ``StagedChange`` under the dispatch ``principal_id``
+ bound scope — it STAGES, it does not APPLY (the executor runs only on a later
user bless). For ``op_class == "execute"`` tools, :func:`_dispatch_direct` runs immediately when
the binding carries a ``direct_ref`` (e.g. ``integral_mark_notification_read``);
otherwise :func:`_dispatch_execute_guard` fail-closes.
"""

from __future__ import annotations

import inspect
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from jvspatial.api.exceptions import JVSpatialAPIException

from app.agentive.staging_language import enrich_filing_tool_result
from app.agentive.tooling.bindings import (
    TOOL_BINDINGS,
    ToolBinding,
    _propose_principal,
    _propose_session_id,
)
from app.agentive.tooling.invoke import invoke_route_in_process
from app.agentive.tooling.list_pagination import (
    agent_list_kwargs,
    expand_agent_list_pages,
    project_agent_list_items,
)
from app.agentive.tooling.manifest import load_manifest
from app.agentive.tooling.policy_gate import enforce_tool_policy, sanitize_tool_args

logger = logging.getLogger(__name__)

_GENERIC_DISPATCH_ERROR = "An internal error occurred while dispatching the tool"
_proposal_only_sessions: Dict[str, float] = {}


def set_proposal_only_guard(session_id: Optional[str]) -> None:
    """Allow discovery and a saved design, but no writes, for this chat turn."""
    if session_id:
        _proposal_only_sessions[session_id] = time.monotonic() + 180.0


def clear_proposal_only_guard(session_id: Optional[str]) -> None:
    """Release a design-only turn's temporary write barrier."""
    if session_id:
        _proposal_only_sessions.pop(session_id, None)


def _proposal_only_guard_active(session_id: Optional[str]) -> bool:
    if not session_id:
        return False
    deadline = _proposal_only_sessions.get(session_id, 0.0)
    if deadline <= time.monotonic():
        _proposal_only_sessions.pop(session_id, None)
        return False
    return True


@dataclass
class ToolResult:
    """Outcome of a :func:`dispatch_tool` call.

    On success: ``data`` carries the handler's return payload, ``is_error`` is
    ``False``. On failure: ``is_error`` is ``True`` with a machine-readable
    ``error_code`` and a human ``message``; ``data`` stays ``None``.
    """

    data: Any = None
    is_error: bool = False
    error_code: str = ""
    message: str = ""


_REGISTRY: Optional[Dict[str, Any]] = None


def _scope_fingerprint(scope: Optional[str]) -> str:
    """Return a log-safe workspace scope token (empty when unscoped)."""
    if not scope:
        return ""
    if len(scope) <= 12:
        return scope
    return f"{scope[:8]}…"


def _missing_required_handler_kwargs(handler: Any, kwargs: Dict[str, Any]) -> list[str]:
    """Names of ``handler``'s required parameters not present in ``kwargs``.

    Skips the leading ``request`` parameter (every route handler's first
    positional arg, supplied internally by ``invoke_route_in_process`` —
    never part of ``kwargs``) and any parameter with a default (optional).
    A required param missing here means the tool's ``param_map`` (commonly
    a ``_pick(...)`` in ``bindings.py``, which silently drops any key not
    present in the model-supplied args rather than raising) failed to
    forward something the handler actually needs — checked BEFORE the
    in-process call so the model gets a clean, actionable error instead of
    a raw ``TypeError`` surfacing as an opaque ``internal_error`` (real
    incident: ``integral_resolve_entry`` called with no ``entry_id``
    crashed as ``get_entry() missing 1 required positional argument:
    'entry_id'`` with no way for the model to tell what went wrong)."""
    try:
        params = list(inspect.signature(handler).parameters.values())
    except (TypeError, ValueError):
        # Signature introspection isn't guaranteed for every callable
        # (e.g. a C-extension or a dynamically-built dispatcher) — fail
        # open rather than block a call this check can't actually reason
        # about; the real invocation still fails closed via
        # ``_tool_error_from_exception`` if something is genuinely wrong.
        return []
    missing = []
    for param in params[1:]:  # skip leading `request`
        if param.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            continue
        if param.default is not inspect.Parameter.empty:
            continue
        if param.name not in kwargs:
            missing.append(param.name)
    return missing


def _tool_error_from_exception(exc: Exception) -> ToolResult:
    """Map a dispatch exception to a fail-closed ToolResult."""
    if isinstance(exc, JVSpatialAPIException):
        return ToolResult(
            is_error=True,
            error_code=exc.error_code,
            message=exc.message,
        )
    from app.agentive.staging import StagingBlockedError

    if isinstance(exc, StagingBlockedError):
        # A propose (or batch commit) refused because an unresolved card
        # already owns the decision. It is a RuntimeError, so the generic
        # branch below used to swallow it into ``internal_error`` and the
        # model never saw WHICH card is waiting or what to do about it.
        return ToolResult(
            is_error=True,
            error_code="staging_blocked",
            message=str(exc),
            data={"blocker": exc.blocker.to_dict()},
        )
    logger.exception("dispatch_tool unhandled error")
    return ToolResult(
        is_error=True,
        error_code="internal_error",
        message=_GENERIC_DISPATCH_ERROR,
    )


def _log_dispatch_metric(
    *,
    tool: str,
    op_class: str,
    result: Optional[ToolResult],
    scope: Optional[str],
    started: float,
) -> None:
    latency_ms = (time.perf_counter() - started) * 1000.0
    error_code = ""
    if result is not None and result.is_error:
        error_code = result.error_code or "error"
    logger.info(
        "dispatch_tool tool=%s op_class=%s error_code=%s latency_ms=%.1f workspace_id=%s",
        tool,
        op_class or "unknown",
        error_code,
        latency_ms,
        _scope_fingerprint(scope),
    )


def _registry() -> Dict[str, Any]:
    """Return the parsed manifest registry, memoized after first load."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = load_manifest()
    return _REGISTRY


def _scope_refusal(
    scope: Optional[str],
    *,
    tool: Optional[str] = None,
    arg: Optional[str] = None,
) -> str:
    """Say no in a way the model can act on.

    "Tool scope cannot widen beyond the bound workspace" is true and useless:
    it does not say which workspace is bound, that the limit is structural
    rather than a permission the user could be granted, or what the user can do
    instead. Without that the model retries, rephrases, and eventually gives up
    mid-task — which is what it did.
    """
    where = f" (workspace {scope})" if scope else ""
    lead = (
        f"`{tool}` ignores `{arg}`: this conversation is scoped to one"
        f" workspace{where}."
        if tool and arg
        else f"This conversation is scoped to one workspace{where}."
    )
    return (
        f"{lead} Workspace scope comes from the conversation, not from tool"
        " arguments, so no argument can retarget it. Do not retry with a"
        " different argument. To work in another workspace, ask the user to"
        " switch to it — the workspace switcher is in the sidebar — and to"
        " repeat the request there."
    )


# Args by which a model tries to aim a tool at a different workspace.
_WORKSPACE_TARGETING_ARGS = ("workspace_id", "workspace")


def _binding_drops(binding: Any, key: str) -> bool:
    """True when we can PROVE ``binding`` discards ``key``.

    Only ``_pick``-built maps advertise their forwarded keys, so a binding
    using a bespoke mapper reads as "might forward" and is left alone. The
    refusal below is therefore conservative: it fires only where the arg is
    provably dropped, never on a tool that might honour it.
    """
    maps = (
        binding.param_map,
        binding.body_map,
        binding.query_map,
        binding.service_param_map,
        binding.direct_param_map,
    )
    saw_declared_map = False
    for mapper in maps:
        if mapper is None:
            continue
        picked = getattr(mapper, "picked_keys", None)
        if picked is None:
            return False  # bespoke mapper — cannot prove anything
        saw_declared_map = True
        if key in picked:
            return False
    return saw_declared_map


async def dispatch_tool(
    name: str,
    args: Optional[Dict[str, Any]],
    *,
    principal_id: str,
    scope: Optional[str],
    session_id: Optional[str] = None,
    interaction_id: Optional[str] = None,
    skill_tools_required: Optional[list] = None,
) -> ToolResult:
    """Dispatch manifest tool ``name`` under ``principal_id`` + bound ``scope``.

    Args:
        name: The manifest tool name (e.g. ``"integral_list_tracks"``).
        args: The tool's call arguments (data only — never identity/scope).
        principal_id: Acting user's AuthUser id; the ONLY identity source.
        scope: Optional workspace id bound as ``X-Integral-Scope``; the ONLY
            scope source. No tool arg can widen it.
        session_id: Optional conversation/session id the propose dispatch minted
            its StagedChange in. Threaded into ``create_staged_change`` so the
            frontend inbox scopes the card to the right conversation and the
            resident's session autonomy-grant can short-circuit the bless. The
            external MCP / consent dispatch surfaces omit it (default ``None``).
        interaction_id: Optional jvagent Interaction id of the prepare-X turn
            that minted the token. Threaded through so the closure-recording path
            updates THAT interaction's response with the
            ``[SYSTEM:STAGING-RESOLVED]`` marker. Omitted off the external
            surfaces (default ``None``). Both apply to ``propose`` staging only.
        skill_tools_required: Optional ADR-002 Phase 1 allowlist. When non-empty,
            ``name`` must be a member or dispatch fail-closes.

    Returns:
        A :class:`ToolResult` — success with ``data``, or fail-closed error.
    """
    started = time.perf_counter()
    op_class = "unknown"
    result: Optional[ToolResult] = None
    try:
        if skill_tools_required:
            from app.agentive.tooling.skill_allowlist import (
                SkillToolAllowlistError,
                assert_tool_allowed_for_skill,
            )

            try:
                assert_tool_allowed_for_skill(name, skill_tools_required)
            except SkillToolAllowlistError as exc:
                result = ToolResult(
                    is_error=True,
                    error_code="skill.tool_not_allowed",
                    message=str(exc),
                )
                return result

        # Prompt Sheet sequester: while the thread has an open queue with
        # unresolved items, refuse further tools. The call that opened / last
        # appended already returned; the model must wait for the user.
        # ``integral_propose_design`` is exempt so a mid-flight amend can
        # replace the pending design card while the sheet is still open
        # (AGENT-01 / AGENT-15).
        from app.services.prompt_queue import session_queue_is_open

        if (
            session_id
            and name != "integral_propose_design"
            and await session_queue_is_open(session_id)
        ):
            result = ToolResult(
                is_error=True,
                error_code="prompt_queue_open",
                message=(
                    "A prompt sheet is open waiting for the user. Do not call "
                    "more tools until they resolve or cancel the prompts."
                ),
            )
            return result

        # Design-propose sequester: after integral_propose_design succeeds on
        # this user-turn, refuse every other tool until the user replies.
        # Soft SOP "STOP" alone lets the model continue into begin_batch on
        # the same turn; this is the mechanical halt (mirrors Prompt Sheet).
        # The propose tool itself is exempt so a first call can land.
        if session_id and name != "integral_propose_design":
            from app.services.chat_threads import (
                design_amend_required,
                design_awaiting_user_response,
            )

            if await design_awaiting_user_response(session_id):
                result = ToolResult(
                    is_error=True,
                    error_code="design_awaiting_user",
                    message=(
                        "A design proposal is waiting for the user. Do not call "
                        "more tools this turn — end your reply and wait for them "
                        "to confirm or correct the shape."
                    ),
                )
                return result

            # Correction turn: pending design is stale until re-proposed.
            # Procedure is in skill integral_scaffold; refuse carries prior body.
            if await design_amend_required(session_id):
                from app.services.chat_threads import get_thread_by_session

                prior = ""
                thread = await get_thread_by_session(session_id)
                if thread is not None:
                    marker = getattr(thread, "design_proposed", None) or {}
                    if isinstance(marker, dict):
                        prior = str(marker.get("proposal") or "").strip()
                        if len(prior) > 6000:
                            prior = prior[:6000] + "\n…(prior proposal truncated)"
                msg = (
                    "design_amend_required: call integral_propose_design with "
                    "the prior proposal plus only the user's deltas (skill "
                    "integral_scaffold). Do not build the stale card."
                )
                if prior:
                    msg = f"{msg}\n\nprior_proposal:\n{prior}"
                result = ToolResult(
                    is_error=True,
                    error_code="design_amend_required",
                    message=msg,
                )
                return result

        spec = _registry().get(name)
        binding = TOOL_BINDINGS.get(name)

        if (
            _proposal_only_guard_active(session_id)
            and name != "integral_propose_design"
            and (spec is None or spec.op_class != "read")
        ):
            result = ToolResult(
                is_error=True,
                error_code="design_proposal_only",
                message=(
                    "This turn asked for an App design only. Read the substrate "
                    "and save the design with integral_propose_design; wait for "
                    "the user's affirmation before any staged or direct write."
                ),
            )
            return result

        # Central manifest/bindings remain authoritative. Bundle-local tools
        # are a workspace-scoped fallback only; they can never shadow or
        # repair a partially-wired central tool.
        if spec is None and binding is None:
            bundle_spec = None
            if scope:
                from app.services.hooks.registry import get_workspace_tools

                bundle_spec = get_workspace_tools(scope).get(name)

            if bundle_spec is not None:
                op_class = "bundle"
                result = await _dispatch_bundle_tool(
                    name,
                    bundle_spec,
                    args or {},
                    principal_id=principal_id,
                    scope=scope,
                    session_id=session_id,
                    interaction_id=interaction_id,
                )
                return result

            result = ToolResult(
                is_error=True,
                error_code="unknown_tool",
                message=f"No such tool: {name}",
            )
            return result

        if spec is None or binding is None:
            result = ToolResult(
                is_error=True,
                error_code="unknown_tool",
                message=f"No such tool: {name}",
            )
            return result

        op_class = spec.op_class

        safe_args = sanitize_tool_args(name, args or {}, scope=scope)
        if safe_args.pop("_scope_violation", False):
            result = ToolResult(
                is_error=True,
                error_code="scope_violation",
                message=_scope_refusal(scope),
            )
            return result

        # A workspace-targeting arg the binding will silently discard is worse
        # than an unsupported one: the tool answers for the BOUND workspace and
        # the model reads that as its filter having been applied. Observed: the
        # resident asked for another workspace's apps, got this one's, decided
        # the API was ignoring the filter, and looped until it gave up. Say no
        # instead, and say what to do about it.
        for _key in _WORKSPACE_TARGETING_ARGS:
            _val = safe_args.get(_key)
            if not isinstance(_val, str) or not _val.strip():
                continue
            if scope and _val.strip() == scope:
                continue
            if _binding_drops(binding, _key):
                result = ToolResult(
                    is_error=True,
                    error_code="scope_violation",
                    message=_scope_refusal(scope, tool=name, arg=_key),
                )
                return result

        policy_err = await enforce_tool_policy(
            spec,
            safe_args,
            principal_id=principal_id,
            workspace_id=scope,
        )
        if policy_err is not None:
            result = policy_err
            return result

        if spec.op_class == "read":
            # SERVICE-backed reads call a service fn directly; checked first so
            # a tool may carry service_ref without a route handler_ref.
            if binding.service_ref is not None:
                result = await _dispatch_service_read(
                    name,
                    binding,
                    safe_args,
                    principal_id=principal_id,
                    scope=scope,
                )
                return result
            if binding.handler_ref is None:
                result = ToolResult(
                    is_error=True,
                    error_code="not_implemented",
                    message=f"{name}: service binding pending",
                )
                return result
            handler = binding.handler_ref()
            # Map args to the handler's keyword arguments. When a body_map /
            # query_map routes the args elsewhere, a None param_map means "no
            # handler kwargs" (the args belong to the JSON body / query string,
            # not the handler signature). Otherwise a None param_map keeps the
            # historical passthrough (handlers that take only ``request`` get an
            # empty-ish args dict either way).
            if binding.param_map is not None:
                kwargs = binding.param_map(safe_args)
            elif binding.body_map is not None or binding.query_map is not None:
                kwargs = {}
            else:
                kwargs = dict(safe_args)
            kwargs = agent_list_kwargs(name, safe_args, kwargs)
            missing = _missing_required_handler_kwargs(handler, kwargs)
            if missing:
                result = ToolResult(
                    is_error=True,
                    error_code="missing_argument",
                    message=(
                        f"{name}: missing required argument(s) " f"{', '.join(missing)}"
                    ),
                )
                return result
            json_body = binding.body_map(safe_args) if binding.body_map else None
            query = binding.query_map(safe_args) if binding.query_map else None

            async def _fetch_list_page(extra: Dict[str, Any]) -> Dict[str, Any]:
                page_kwargs = {**kwargs, **extra}
                return await invoke_route_in_process(
                    handler,
                    principal_id=principal_id,
                    scope=scope,
                    json_body=json_body,
                    query=query,
                    **page_kwargs,
                )

            data = await _fetch_list_page({})
            if isinstance(data, dict) and data.get("error"):
                result = ToolResult(
                    is_error=True,
                    error_code=data.get("error_code", "error"),
                    message=data.get("message", ""),
                )
                return result
            if isinstance(data, dict):
                data = await expand_agent_list_pages(
                    name,
                    safe_args=safe_args,
                    first_page=data,
                    fetch_page=_fetch_list_page,
                )
            # Narrow listed items to the fields a listing is for. AFTER the
            # expansion above so it applies to the merged set, and outside the
            # isinstance guard so an explicitly-paginated call (which returns
            # early from the expander) is projected too.
            data = project_agent_list_items(name, data)
            result = ToolResult(data=data)
            return result

        if spec.op_class == "propose":
            result = await _dispatch_propose(
                spec,
                binding,
                safe_args,
                principal_id=principal_id,
                scope=scope,
                session_id=session_id,
                interaction_id=interaction_id,
            )
            return result

        if spec.op_class == "execute":
            if binding.direct_ref is not None:
                result = await _dispatch_direct(
                    binding, safe_args, principal_id=principal_id, scope=scope
                )
                return result
            result = _dispatch_execute_guard(spec, safe_args)
            return result

        # Unknown op-class (should be impossible — ToolSpec.op_class is a
        # Literal). Fail closed rather than guessing a dispatch path.
        result = ToolResult(
            is_error=True,
            error_code="not_implemented",
            message=f"op_class {spec.op_class} not yet wired",
        )
        return result
    except Exception as exc:  # JVSpatialAPIException etc. -> fail-closed envelope
        result = _tool_error_from_exception(exc)
        return result
    finally:
        _log_dispatch_metric(
            tool=name,
            op_class=op_class,
            result=result,
            scope=scope,
            started=started,
        )


async def _maybe_stage_mcp_write(
    name: str,
    spec: Dict[str, Any],
    args: Dict[str, Any],
    *,
    principal_id: str,
    scope: Optional[str],
    session_id: Optional[str],
    interaction_id: Optional[str],
) -> Optional[ToolResult]:
    """Stage a write-classified mounted MCP call; return None to invoke directly.

    Returns a ToolResult describing the pending approval when the call was
    staged, or ``None`` when the tool is catalog-certified read-only and may
    run immediately.
    """
    from app.agentive.connectors.mcp_tool_class import (
        describe_tool_call,
        is_write_tool,
        remote_says_read_only,
    )
    from app.agentive.nodes import Connector
    from app.agentive.services.connector_registry_node import decrypt_auth_state

    connector_id = str(spec.get("_mcp_connector_id") or "")
    connector = await Connector.get(connector_id)
    auth_state = decrypt_auth_state(getattr(connector, "auth_state", None) or {})
    if not is_write_tool(spec, auth_state=auth_state):
        return None

    display_name = str(auth_state.get("display_name") or "")
    summary = describe_tool_call(spec, args, display_name=display_name)
    lines = [summary]
    if remote_says_read_only(spec):
        # Surface the remote's claim; never act on it.
        lines.append(
            "Note: the server reports this tool as read-only. That claim is "
            "not verified — approve only if you expect this call."
        )
    for key, value in list(args.items())[:12]:
        lines.append(f"  {key}: {value}")

    from app.agentive.staging import create_staged_change

    sc = await create_staged_change(
        user_id=principal_id,
        session_id=session_id,
        kind="mcp_tool_call",
        summary=summary,
        diff_human="\n".join(lines),
        diff_machine={
            "connector_id": connector_id,
            "remote_name": str(spec.get("_mcp_remote_name") or ""),
            "args": dict(args),
        },
        payload={
            "connector_id": connector_id,
            "remote_name": str(spec.get("_mcp_remote_name") or ""),
            # Bound from the ACTIVE scope, never from model args.
            "workspace_id": scope or "",
            "args": dict(args),
        },
        interaction_id=interaction_id,
    )
    # Put the card in front of the user. Without this the token exists but the
    # only surfaces that render it are the Inbox and Approvals rows, which show
    # ``summary`` alone — so the person approving a write into a third-party
    # system never sees the arguments being sent. ``diff_human`` (composed
    # above, with the per-arg lines) is rendered by the Prompt Sheet, and this
    # is what puts it there. Every other propose path already does this; this
    # one did not, which made it the least-informed approval in the product
    # while being the only one that leaves the workspace.
    if session_id is not None:
        from app.services.prompt_queue import enqueue_staged_write

        await enqueue_staged_write(
            user_id=principal_id,
            session_id=session_id,
            staged=sc.to_dict(),
        )
    return ToolResult(
        is_error=False,
        data={
            "staged": True,
            "token": sc.token,
            "state": sc.state,
            "summary": summary,
            "message": (
                f"{name} reaches an external system, so it needs approval. "
                "Staged for the user to review; do not retry."
            ),
        },
    )


async def _maybe_stage_native_write(
    name: str,
    spec: Dict[str, Any],
    args: Dict[str, Any],
    *,
    principal_id: str,
    scope: Optional[str],
    session_id: Optional[str],
    interaction_id: Optional[str],
) -> Optional[ToolResult]:
    """Stage a write-classified native Google call; None to invoke directly.

    Classification is the vetted ``_native_write`` flag stamped on the spec
    at registration (first-party ``TOOL_SPECS`` — the only source consulted,
    paralleling the catalog ``read_only_tools`` gate for MCP). The staged
    kind is ``native_tool_call`` (executor in ``staging_executors``), with
    ``remote_name`` set to the native tool name so session-autonomy scoping
    (``autonomy_key_for``) narrows to the concrete target.
    """
    if not spec.get("_native_write"):
        return None

    connector_id = str(spec.get("_native_connector_id") or "")
    tool_name = str(spec.get("_native_tool_name") or "")
    summary = f"Google {tool_name} (native)"
    lines = [summary]
    for key, value in list(args.items())[:12]:
        rendered = str(value)
        if len(rendered) > 300:
            rendered = rendered[:299] + "…"
        lines.append(f"  {key}: {rendered}")

    from app.agentive.staging import create_staged_change

    sc = await create_staged_change(
        user_id=principal_id,
        session_id=session_id,
        kind="native_tool_call",
        summary=summary,
        diff_human="\n".join(lines),
        diff_machine={
            "connector_id": connector_id,
            "remote_name": tool_name,
            "args": dict(args),
        },
        payload={
            "connector_id": connector_id,
            "remote_name": tool_name,
            "workspace_id": scope or "",
            "connector_slug": str(spec.get("_native_connector_slug") or ""),
            "args": dict(args),
        },
        interaction_id=interaction_id,
    )
    # Put the card in front of the user (same Prompt Sheet contract as the
    # other staging paths).
    return ToolResult(
        data={
            "staged": True,
            "staged_change_id": sc.id,
            "summary": summary,
            "message": (
                f"I've prepared the Google {tool_name} call for your approval. "
                "Review the parameters and approve when you're ready."
            ),
        }
    )


async def _dispatch_bundle_tool(
    name: str,
    spec: Dict[str, Any],
    args: Dict[str, Any],
    *,
    principal_id: str,
    scope: Optional[str],
    session_id: Optional[str] = None,
    interaction_id: Optional[str] = None,
) -> ToolResult:
    """Dispatch one trusted bundle-local tool registered in ``scope``.

    The per-workspace registry is populated only for bundle tools that passed
    manifest trust checks during App install/rehydration. Invocation reuses the
    existing hook-framework ``run_tool`` path, which validates input/output
    schemas, resolves the normalized handler_ref, invokes ``fn(payload, ctx)``,
    and emits the tool audit event.

    Identity and workspace scope are supplied exclusively by dispatch context.
    A bundle tool never receives either from model-provided args.

    ``privileged: true`` keeps the existing bundle-tool rule: only a workspace
    owner/admin may invoke the tool.
    """
    if not scope:
        return ToolResult(
            is_error=True,
            error_code="scope_required",
            message=f"{name}: bundle-local tools require a bound workspace",
        )
    if not principal_id:
        return ToolResult(
            is_error=True,
            error_code="principal_required",
            message=f"{name}: bundle-local tools require an acting principal",
        )

    if bool(spec.get("privileged", False)):
        from app.services.permissions import resolve_role

        role = await resolve_role(principal_id, "workspace", scope)
        if role not in ("owner", "admin"):
            return ToolResult(
                is_error=True,
                error_code="permission_denied",
                message=f"{name}: workspace admin access is required",
            )

    # Validate BEFORE the bless gate: a schema-invalid call can never
    # execute, so it must fail fast instead of minting an approval card the
    # user might bless.
    try:
        from app.services.hooks.tool_dispatch import validate_input

        validate_input(
            dict(args or {}),
            spec.get("parameters_schema") or spec.get("input_schema") or {},
        )
    except Exception as exc:  # noqa: BLE001 — validation errors fail fast
        return _tool_error_from_exception(exc)

    # ADR-010 §6 — nothing leaves the workspace without a human bless.
    # A mounted MCP tool reaches a third party, so a write-classified call is
    # STAGED rather than invoked: the resident proposes, the user approves, and
    # the executor makes the call. Classification is default-deny and consults
    # only the vetted catalog — the remote's own readOnlyHint is supplied by
    # the party this gate constrains (see connectors/mcp_tool_class.py).
    #
    # Native Google tools follow the same contract with first-party specs:
    # a write-classified call stages as ``native_tool_call`` (see
    # ``_maybe_stage_native_write``).
    #
    # Only the resident path stages. A human calling the tool directly IS the
    # approver, and has no session to hang a card on.
    if session_id and spec.get("_mcp_connector_id"):
        staged = await _maybe_stage_mcp_write(
            name,
            spec,
            args or {},
            principal_id=principal_id,
            scope=scope,
            session_id=session_id,
            interaction_id=interaction_id,
        )
        if staged is not None:
            return staged
    if session_id and spec.get("_native_connector_id"):
        staged = await _maybe_stage_native_write(
            name,
            spec,
            args or {},
            principal_id=principal_id,
            scope=scope,
            session_id=session_id,
            interaction_id=interaction_id,
        )
        if staged is not None:
            return staged

    from app.services.hooks.registry import ToolContext
    from app.services.hooks.tool_dispatch import run_tool

    ctx = ToolContext(
        user_id=principal_id,
        workspace_id=scope,
        scope=scope,
        actor_kind="agent",
    )
    data = await run_tool(spec, dict(args or {}), ctx)
    return ToolResult(data=data)


async def _dispatch_service_read(
    tool_name: str,
    binding: ToolBinding,
    args: Dict[str, Any],
    *,
    principal_id: str,
    scope: Optional[str],
) -> ToolResult:
    """Dispatch a SERVICE-backed read: ``await fn(user_id=principal_id, **kw)``.

    Identity is non-negotiable: the service's ``user_id`` is the dispatch
    ``principal_id``, NEVER a tool arg (PC-1). Workspace scope is bound by
    setting the ``current_scope_workspace_id`` ContextVar (the same seam the
    resident embedded action uses) to the dispatch ``scope`` around the call,
    then reset in ``finally`` so a service call can't leak scope into a later
    call (PC-2). ``service_param_map`` supplies NON-identity kwargs only.

    Scope binding has TWO seams, both fed by the dispatch ``scope`` (never an
    arg — PC-2):

    * the ``current_scope_workspace_id`` ContextVar (legacy seam — services that
      read it internally, and the resident embedded action, honor it), AND
    * an explicit ``workspace_id=<scope>`` kwarg for services whose signature
      accepts one (e.g. ``agent_insights.count_entries_grouped`` /
      ``activity_digest``, which apply the B-AGENT-03 workspace gate ONLY when
      ``workspace_id`` is passed — absent it they fall back to cross-workspace
      behaviour and would leak records from a workspace the caller is scoped out
      of). We inject it by signature introspection so the existing scope-free
      services (``describe_operational_model`` / the draft helpers, none
      of which take ``workspace_id``) are unaffected. An explicit ``workspace_id``
      from ``service_param_map`` (there is none today) would NOT be overridden —
      the bound scope only fills an otherwise-absent kwarg.

    Fail-closed: a service raising (e.g. a permission ``JVSpatialAPIException``)
    propagates to :func:`dispatch_tool`'s handler, which envelopes it as an
    error ToolResult. A service that RETURNS a structured error dict
    (``{"error": ...}``) — the convention in ``operational_model_authoring`` — is normalized
    into an error ToolResult here so the caller never sees a leaky success.
    """
    from app.services.agent_scope import current_scope_workspace_id

    service_fn = binding.service_ref()  # type: ignore[misc]
    service_param_map = binding.service_param_map or (lambda a: dict(a or {}))
    kwargs = service_param_map(args)

    # Inject the bound workspace scope as ``workspace_id`` for services that
    # accept it (PC-2). The bound ``scope`` is the dispatch workspace, never a
    # tool arg; passing it is what makes a SERVICE read workspace-scoped. Only
    # fills the kwarg when the fn declares ``workspace_id`` AND the map did not
    # already supply one — so scope-free services are untouched and an explicit
    # mapped value (none exists today) is never clobbered.
    if "workspace_id" not in kwargs:
        try:
            sig = inspect.signature(service_fn)
        except (TypeError, ValueError):  # builtins / C-callables: no signature
            sig = None
        if sig is not None and "workspace_id" in sig.parameters:
            kwargs["workspace_id"] = scope

    token = current_scope_workspace_id.set(scope)
    try:
        data = await service_fn(user_id=principal_id, **kwargs)
    finally:
        current_scope_workspace_id.reset(token)

    # Services in operational_model_authoring return ``{"error": "<code>", "detail": ...}``
    # for permission/not-found refusals rather than raising. Normalize those
    # to an error ToolResult so a refusal never reads as a success payload.
    if isinstance(data, dict) and data.get("error"):
        return ToolResult(
            is_error=True,
            error_code=str(data.get("error") or "error"),
            message=str(data.get("detail") or data.get("message") or ""),
        )
    return ToolResult(data=enrich_filing_tool_result(tool_name, data))


async def _dispatch_propose(
    spec: Any,
    binding: ToolBinding,
    args: Dict[str, Any],
    *,
    principal_id: str,
    scope: Optional[str],
    session_id: Optional[str] = None,
    interaction_id: Optional[str] = None,
) -> ToolResult:
    """Dispatch a PROPOSE tool: run its stager, mint a pending StagedChange.

    A propose dispatch STAGES, it does NOT APPLY. The tool's
    :attr:`ToolBinding.stager` maps the call args to ``create_staged_change``
    kwargs (``kind`` / ``summary`` / ``diff_human`` / ``diff_machine`` /
    ``payload``); we then mint a pending ``StagedChange`` whose ``user_id`` is
    the dispatch ``principal_id`` (identity is NEVER from the stager / args —
    PC-1). The underlying mutation runs only when the user later blesses the
    token (the bless endpoint dispatches ``staging_executors``); nothing is
    written here.

    Scope discipline (PC-2): the workspace scope is bound via the
    ``current_scope_workspace_id`` ContextVar around the stager so a stager that
    reads substrate to build a faithful diff/payload (e.g. ``create_entry``
    resolving track hints via ``filing_resolution``) honors the bound workspace; the
    acting principal is likewise bound via the tooling ``_propose_principal``
    ContextVar for the same window (an async stager needs a ``user_id`` for its
    read but must not receive identity through its signature). Both are reset in
    ``finally`` so neither leaks into a later, differently-scoped call.

    Stagers may be SYNC (the common case) or ASYNC (when a faithful payload
    needs a substrate read); a coroutine result is awaited.

    Session threading: ``session_id`` and ``interaction_id`` flow straight
    through to ``create_staged_change`` (NOT from the stager / args — they are
    dispatch context). ``session_id`` scopes the frontend inbox card to the
    minting conversation and lets the resident's session autonomy-grant
    short-circuit the bless; ``interaction_id`` lets the closure-recording path
    update the prepare-X interaction's response with the
    ``[SYSTEM:STAGING-RESOLVED]`` marker. Both default ``None`` so the external
    MCP / consent dispatch surfaces (which omit them) stage exactly as before.

    No-stage escape: a stager may return ``{"_no_stage": True, "data": ...}`` to
    signal "don't stage — return this data directly." We surface that ``data`` in
    a non-error ``ToolResult`` and mint NO StagedChange (there is nothing to
    bless). Used by the ``file_content`` unresolved branch, which returns
    candidate matches for the agent to disambiguate rather than staging a write.

    Fail-closed: a tool with no ``stager`` returns ``not_implemented``; any
    stager raise (missing/unresolvable args, a substrate read it can't satisfy)
    or a ``create_staged_change`` failure propagates to :func:`dispatch_tool`'s
    broad ``except`` and becomes an error ToolResult — never a partial write.

    Direct-execute exception (PC-8 carve-out): a ``propose``-classified tool may
    be EPHEMERAL — its effect is conversation/session state, not a substrate
    write — and therefore carry a ``direct_ref`` instead of a ``stager``. There
    is nothing to bless, so it runs IMMEDIATELY via :func:`_dispatch_direct`
    rather than minting a StagedChange. ``integral_set_focus`` is the sole such
    tool today (it stays in ``_STAGING_EXEMPT_PROPOSE_TOOLS`` since it never
    mints a token). Checked first so a direct_ref tool need not carry a stager.
    """
    # Batch control (begin / commit / cancel) — these need ``session_id``,
    # which lives in dispatch context, so they are handled here by name rather
    # than via a direct_ref binding (which does not carry session_id). They
    # write no substrate themselves; begin/cancel return a status, commit mints
    # the single batch StagedChange card.
    if spec.name in _BATCH_CONTROL_TOOLS:
        return await _dispatch_batch_control(
            spec.name,
            dict(args or {}),
            principal_id=principal_id,
            scope=scope,
            session_id=session_id,
            interaction_id=interaction_id,
        )

    if spec.name == "integral_build_approved_design":
        from app.agentive.tooling.scaffold_build import build_approved_design

        return await build_approved_design(
            dict(args or {}),
            principal_id=principal_id,
            scope=scope,
            session_id=session_id,
            interaction_id=interaction_id,
        )

    # A chat-created App is a greenfield scaffold, not an isolated CRUD
    # mutation.  If the model merely describes a design in prose and then
    # calls create_app, the batch cannot bind the user's later affirmation to
    # that design; the result is a second, technical approval card.  Refuse
    # before staging so the model must record the visible proposal through the
    # same contract that commit_batch and recovery consume.
    if spec.name == "integral_create_app" and session_id is not None:
        from app.services.chat_threads import (
            design_proposed_pending,
            recover_visible_design_for_affirmed_build,
        )

        if not await design_proposed_pending(session_id):
            recovered = await recover_visible_design_for_affirmed_build(
                user_id=principal_id,
                session_id=session_id,
                summary=str((args or {}).get("name") or "Affirmed app design"),
            )
            if recovered is None:
                return ToolResult(
                    is_error=True,
                    error_code="design_required",
                    message=(
                        "Before creating a new app in chat, call "
                        "integral_propose_design with the complete plain-language "
                        "design, reply with that design, and wait for the user's "
                        "confirm or correction. Do not stage a standalone create_app."
                    ),
                )
            # Preserve the recovered visible proposal in the same session
            # artifact contract used by the explicit design tool.
            from app.agentive.artifacts import upsert_artifact

            await upsert_artifact(
                user_id=principal_id,
                session_id=session_id,
                key="app_design_blueprint",
                kind="app_design_blueprint",
                title=recovered["summary"],
                body=recovered["proposal"],
                metadata={"source": "visible_chat_design_affirm"},
            )

    if spec.name == "integral_propose_design":
        from app.agentive.artifacts import upsert_artifact
        from app.services.chat_threads import record_design_proposed

        _args = dict(args or {})
        result = await record_design_proposed(
            user_id=principal_id,
            session_id=session_id,
            summary=str(_args.get("summary") or ""),
            proposal=str(_args.get("proposal") or ""),
            acceptance_assertions=list(_args.get("acceptance_assertions") or []),
            target_app_id=str(_args.get("target_app_id") or ""),
        )
        if result.get("error"):
            return ToolResult(
                is_error=True,
                error_code=str(result.get("error")),
                message=str(result.get("detail") or result.get("error")),
            )
        # Persist blueprint as a session artifact so any harness can re-read
        # the agreed shape without Integral-specific UI cards.
        art_key = "app_design_blueprint"
        art = await upsert_artifact(
            user_id=principal_id,
            session_id=session_id,
            key=art_key,
            kind="app_design_blueprint",
            title=str(result.get("summary") or "App design"),
            body=str(result.get("proposal") or ""),
            metadata={"source": "integral_propose_design"},
        )
        if not art.get("error"):
            from app.services.chat_threads import get_thread_by_session

            thread = await get_thread_by_session(session_id)
            if thread is not None:
                marker = dict(getattr(thread, "design_proposed", None) or {})
                marker["artifact_key"] = art_key
                thread.design_proposed = marker
                await thread.save()
        data = {
            "_kind": "design_outline",
            "summary": result.get("summary"),
            "proposal": result.get("proposal"),
            "acceptance_assertions": result.get("acceptance_assertions") or [],
            "replaced": result.get("replaced"),
            "artifact_key": art_key if not art.get("error") else None,
            "artifact_version": art.get("version"),
            "message": result.get("message")
            or (
                "Design outline recorded. Put the FULL proposal markdown in "
                "your reply text (the user reads chat, not a card), then STOP "
                "and wait for confirm or correct. Do not begin_batch yet."
            ),
        }
        if result.get("prior_proposal"):
            data["prior_proposal"] = result["prior_proposal"]
        return ToolResult(data=data)

    if spec.name in {
        "integral_upsert_artifact",
        "integral_get_artifact",
        "integral_list_artifacts",
    }:
        from app.agentive import artifacts as artifact_svc

        _args = dict(args or {})
        if spec.name == "integral_upsert_artifact":
            out = await artifact_svc.upsert_artifact(
                user_id=principal_id,
                session_id=session_id,
                key=str(_args.get("key") or ""),
                kind=str(_args.get("kind") or ""),
                title=str(_args.get("title") or ""),
                body=str(_args.get("body") or ""),
                metadata=(
                    _args.get("metadata")
                    if isinstance(_args.get("metadata"), dict)
                    else None
                ),
            )
        elif spec.name == "integral_get_artifact":
            out = await artifact_svc.get_artifact(
                user_id=principal_id,
                session_id=session_id,
                key=str(_args.get("key") or ""),
            )
        else:
            out = await artifact_svc.list_artifacts(
                user_id=principal_id,
                session_id=session_id,
                kind=str(_args.get("kind") or "") or None,
            )
        if out.get("error"):
            return ToolResult(
                is_error=True,
                error_code=str(out.get("error")),
                message=str(out.get("detail") or out.get("error")),
            )
        return ToolResult(data=out)

    if spec.name == "integral_ask_user":
        from app.services.prompt_queue import enqueue_questions

        _args = dict(args or {})
        raw_questions = _args.get("questions")
        if isinstance(raw_questions, list) and raw_questions:
            questions = [dict(q) if isinstance(q, dict) else {} for q in raw_questions]
        else:
            questions = [
                {
                    "question": str(_args.get("question") or ""),
                    "options": _args.get("options"),
                    "header": str(_args.get("header") or ""),
                    "multi_select": bool(_args.get("multi_select") or False),
                    "allow_other": bool(_args.get("allow_other") or False),
                }
            ]
        result = await enqueue_questions(
            user_id=principal_id,
            session_id=session_id,
            questions=questions,
        )
        if result.get("error"):
            return ToolResult(
                is_error=True,
                error_code=str(result.get("error")),
                message=str(result.get("detail") or result.get("error")),
            )
        return ToolResult(data=result)

    if binding.direct_ref is not None:
        return await _dispatch_direct(
            binding, args, principal_id=principal_id, scope=scope
        )
    if binding.stager is None:
        return ToolResult(
            is_error=True,
            error_code="not_implemented",
            message=f"{spec.name}: propose staging pending",
        )

    from app.services.agent_scope import current_scope_workspace_id

    scope_token = current_scope_workspace_id.set(scope)
    principal_token = _propose_principal.set(principal_id)
    session_token = _propose_session_id.set(session_id)
    try:
        staged = binding.stager(dict(args or {}))
        if inspect.isawaitable(staged):
            staged = await staged
    finally:
        _propose_session_id.reset(session_token)
        _propose_principal.reset(principal_token)
        current_scope_workspace_id.reset(scope_token)

    # No-stage escape: a stager may signal "don't stage — return this data
    # directly" by yielding ``{"_no_stage": True, "data": ...}``. There is
    # nothing to bless, so we return the carried data immediately and mint NO
    # StagedChange. (Used by the ``file_content`` unresolved branch, which
    # returns candidate matches for the agent to disambiguate rather than
    # staging a write.) Checked BEFORE the create_staged_change block so the
    # escape never touches the token store.
    if isinstance(staged, dict) and staged.get("_no_stage"):
        return ToolResult(data=enrich_filing_tool_result(spec.name, staged.get("data")))

    from app.agentive.staging import (
        append_to_batch,
        create_staged_change,
        is_batch_open,
        open_batch,
    )
    from app.services.chat_threads import design_proposed_pending

    # After integral_propose_design, scaffold writes must share one batch.
    # Models often call create_app before begin_batch; hard-refusing
    # (batch_required) derailed the affirm turn (repeat_guard + prose-only
    # "Building…" with zero apps). Auto-open so the first write stages.
    batch_auto_opened = False
    if (
        session_id is not None
        and not is_batch_open(principal_id, session_id)
        and await design_proposed_pending(session_id)
    ):
        await open_batch(
            user_id=principal_id,
            session_id=session_id,
            label="Scaffold build",
        )
        batch_auto_opened = True

    # Batch interception: when a skill has an open batch for this session, the
    # staged op is ACCUMULATED rather than minting its own token. One bless at
    # ``integral_commit_batch`` then applies the whole workflow. The op carries
    # only stager output (no identity/scope — PC-1/PC-2 preserved).
    if session_id is not None and is_batch_open(principal_id, session_id):
        # Greenfield scaffold: refuse orphan tracks (no app) even inside a batch —
        # they would bless as "App: (no app)" and break {{app.id}} wiring.
        if staged.get("kind") == "create_track" and await design_proposed_pending(
            session_id
        ):
            payload = staged.get("payload") or {}
            if not (payload.get("app_id") or payload.get("space_id")):
                return ToolResult(
                    is_error=True,
                    error_code="scaffold_track_requires_app",
                    message=(
                        "create_track needs app_id during a scaffold build — use "
                        'integral_create_app_track with app_id="{{app.id}}" after '
                        "integral_create_app inside the same batch."
                    ),
                )
        count = await append_to_batch(
            user_id=principal_id,
            session_id=session_id,
            op={
                "kind": staged["kind"],
                "summary": staged["summary"],
                "diff_human": staged["diff_human"],
                "diff_machine": staged["diff_machine"],
                "payload": staged["payload"],
            },
        )
        batched: Dict[str, Any] = {
            "_kind": "batched_op",
            "batched": True,
            "kind": staged["kind"],
            "summary": staged["summary"],
            "batch_size": count,
            "batch_auto_opened": batch_auto_opened,
            "next": (
                "Batch is open. Stage remaining scaffold ops "
                '(create_app_track with app_id="{{app.id}}", views, demo '
                "entries as needed), then call integral_commit_batch. Do not "
                "tell the user the app exists until commit returns "
                "batch_applied / applied=true."
            ),
        }
        if batch_auto_opened:
            batched["note"] = (
                "Batch was opened automatically because a design proposal is "
                "pending — no separate integral_begin_batch call is required."
            )
        return ToolResult(data=batched)

    from app.agentive.unstaged_targets import is_unstaged_target

    # ADR-006 / I-PC-01 — the one write path that skips the card.
    #
    # An App may declare tracks that accept agent writes unstaged, and the
    # gate below additionally requires the App to live in the acting
    # principal's OWN personal workspace with that principal as its owner.
    # The write still runs through the SAME executor a bless would run, so
    # there is one write path, not two: this decides whether a card is minted,
    # never how the write happens.
    #
    # Checked after the batch interception on purpose. A skill that opened a
    # batch asked for one card covering the whole workflow, and honouring that
    # is more useful than exempting some of its ops — an exempt op inside a
    # batch would land early and out of order with the rest.
    if await is_unstaged_target(
        user_id=principal_id, kind=staged["kind"], payload=staged["payload"]
    ):
        from app.agentive.staging_executors import dispatch as execute_staged

        result = await execute_staged(
            user_id=principal_id,
            kind=staged["kind"],
            payload=staged["payload"],
        )
        if isinstance(result, dict) and result.get("error"):
            return ToolResult(
                is_error=True,
                error_code=str(result.get("error_code") or "unstaged_write_failed"),
                message=str(result.get("message") or "unstaged write failed"),
            )
        return ToolResult(
            data={
                "_kind": "unstaged_write",
                "staged": False,
                "kind": staged["kind"],
                "summary": staged["summary"],
                "result": result,
            }
        )

    sc = await create_staged_change(
        user_id=principal_id,
        session_id=session_id,
        kind=staged["kind"],
        summary=staged["summary"],
        diff_human=staged["diff_human"],
        diff_machine=staged["diff_machine"],
        payload=staged["payload"],
        interaction_id=interaction_id,
    )
    data = enrich_filing_tool_result(spec.name, sc.to_dict())
    if session_id is not None:
        from app.services.prompt_queue import enqueue_staged_write

        await enqueue_staged_write(
            user_id=principal_id,
            session_id=session_id,
            staged=sc.to_dict(),
        )
    return ToolResult(data=data)


_BATCH_CONTROL_TOOLS = frozenset(
    {"integral_begin_batch", "integral_commit_batch", "integral_cancel_batch"}
)


async def _dispatch_batch_control(
    name: str,
    args: Dict[str, Any],
    *,
    principal_id: str,
    scope: Optional[str],
    session_id: Optional[str],
    interaction_id: Optional[str],
    approved_extension: bool = False,
) -> ToolResult:
    """Open / commit / cancel a staging batch for the acting (user, session).

    Batches require a session to scope the open-batch store; the external-MCP /
    consent surfaces that omit ``session_id`` cannot batch (each propose stages
    on its own), so these tools fail closed there with a clear message.
    """
    from app.services.agent_scope import current_scope_workspace_id

    if session_id is None:
        return ToolResult(
            is_error=True,
            error_code="batch_requires_session",
            message=f"{name}: batching requires a conversation session",
        )

    # A batch control call does not pass through the ordinary stager path,
    # which is where scope is usually bound.  Commit mints the batch token and
    # the token must retain the workspace in which its individual operations
    # were authored; otherwise execution silently falls back to Personal and
    # the follow-up scoped list read appears empty.
    scope_token = current_scope_workspace_id.set(scope)
    try:
        return await _dispatch_batch_control_in_scope(
            name,
            args,
            principal_id=principal_id,
            session_id=session_id,
            interaction_id=interaction_id,
            approved_extension=approved_extension,
        )
    finally:
        current_scope_workspace_id.reset(scope_token)


async def _dispatch_batch_control_in_scope(
    name: str,
    args: Dict[str, Any],
    *,
    principal_id: str,
    session_id: str,
    interaction_id: Optional[str],
    approved_extension: bool = False,
) -> ToolResult:
    """Execute batch control with the dispatch workspace already bound."""
    from app.agentive.staging import (
        cancel_batch,
        commit_batch,
        open_batch,
        peek_open_batch,
    )

    if name == "integral_begin_batch":
        from app.services.chat_threads import design_chat_affirmed_for_build

        if (
            not peek_open_batch(principal_id, session_id)
            and args.get("manual_recovery") is not True
            and await design_chat_affirmed_for_build(session_id)
        ):
            return ToolResult(
                is_error=True,
                error_code="use_approved_build_tool",
                message=(
                    "This App design was already approved. Call "
                    "integral_build_approved_design with the complete plan; "
                    "correct preflight errors there without asking for approval again."
                ),
            )
        await open_batch(
            user_id=principal_id,
            session_id=session_id,
            label=str(args.get("label") or ""),
        )
        return ToolResult(data={"_kind": "batch_opened", "batch_open": True})

    if name == "integral_cancel_batch":
        existed = await cancel_batch(user_id=principal_id, session_id=session_id)
        return ToolResult(data={"_kind": "batch_cancelled", "cancelled": existed})

    # integral_commit_batch
    from app.agentive.staging import StagingError

    # Capture BEFORE commit clears the design marker.
    from app.services.chat_threads import design_chat_affirmed_for_build

    chat_affirmed_design = await design_chat_affirmed_for_build(session_id)

    try:
        sc = await commit_batch(
            user_id=principal_id,
            session_id=session_id,
            summary=str(args.get("summary") or "") or None,
            allow_empty=args.get("allow_empty") is True,
            interaction_id=interaction_id,
        )
    except StagingError as exc:
        # Soft-continue for incomplete greenfield: a hard error used to make
        # the model stop mid-build and LIE that a WRITE · BATCH card was ready
        # (live 2026-09-19). Keep batch_open + missing inventory so the loop
        # can append and re-commit.
        #
        # When the user already chat-affirmed, soft-continue still let the
        # model reply with "staged for approval" after create_app+profile
        # only (0 tracks, 0 apps). Mark that case as an error so the loop
        # keeps tool-calling instead of narrating.
        if exc.code == "incomplete_scaffold":
            from app.agentive.staging import peek_open_batch

            snap = peek_open_batch(principal_id, session_id) or {}
            missing = snap.get("missing") or []
            data = {
                "_kind": "batch_incomplete",
                "ready": False,
                "batch_open": True,
                "error_code": exc.code,
                "message": str(exc),
                "op_count": snap.get("op_count", 0),
                "kinds": snap.get("kinds") or [],
                "missing": missing,
                "next": (
                    "Batch stays open. Append EVERY item in missing "
                    f"({'; '.join(missing) or 'see message'}), then "
                    "integral_commit_batch again. Do NOT reply to the user "
                    "and do NOT claim a Prompt Sheet card or app exists yet."
                ),
            }
            return ToolResult(
                is_error=bool(chat_affirmed_design),
                error_code=exc.code if chat_affirmed_design else "",
                message=(
                    f"{exc}; next: append missing then recommit"
                    if chat_affirmed_design
                    else ""
                ),
                data=data,
            )
        return ToolResult(
            is_error=True,
            error_code=exc.code,
            message=str(exc),
        )
    if sc is None:
        return ToolResult(
            data={"_kind": "batch_empty", "batched": False, "op_count": 0}
        )
    data = sc.to_dict()
    ops = (data.get("diff_machine") or {}).get("operations") or []
    is_greenfield = any(
        isinstance(op, dict)
        and op.get("kind") in ("create_app", "author_operational_model")
        for op in ops
    )
    # Chat affirm of the design IS the approval for a builder-owned scaffold —
    # apply now; do not mint a Prompt Sheet bless card (product: no second gate).
    if chat_affirmed_design and (is_greenfield or approved_extension):
        from app.agentive.services.staging_apply import bless_and_execute
        from app.agentive.staging import StagingError as _StagingApplyError

        try:
            applied = await bless_and_execute(
                user_id=principal_id,
                token=sc.token,
                autonomy="single",
                request=None,
            )
        except _StagingApplyError as exc:
            return ToolResult(
                is_error=True,
                error_code=exc.code,
                message=str(exc),
                data=(
                    {"batch_token": sc.token}
                    if exc.code == "batch_partial_failure"
                    else None
                ),
            )
        data["applied"] = bool(applied.get("consumed"))
        data["execute_result"] = applied.get("execute_result")
        data["staged_change"] = applied.get("staged_change") or data
        op_count = len(ops)
        if data["applied"] and not (
            isinstance(data["execute_result"], dict)
            and data["execute_result"].get("error")
        ):
            from app.services.chat_threads import record_design_build_receipt

            await record_design_build_receipt(
                session_id=session_id,
                user_id=principal_id,
                batch_token=sc.token,
            )
            data["_kind"] = "batch_applied"
            data["message"] = (
                f"Approved design applied ({op_count} step(s)). Read back the "
                "App and its new tracks before reporting verification. "
                "Do not ask for another approval."
            )
        else:
            data["_kind"] = "batch_apply_failed"
            data["message"] = (
                "Chat-affirmed build was approved but the write failed. "
                "Inspect execute_result, fix, and recover — do not claim the "
                "app exists."
            )
        return ToolResult(data=data)

    from app.services.prompt_queue import enqueue_staged_write

    await enqueue_staged_write(
        user_id=principal_id,
        session_id=session_id,
        staged=data,
    )
    op_count = len(ops)
    data["message"] = (
        f"Build staged ({op_count} step(s)) for user approval. STOP — wait for "
        "them to Approve the Prompt Sheet card. Do NOT claim the app or tracks "
        "exist until that approval consumes the batch."
    )
    return ToolResult(data=data)


async def _dispatch_direct(
    binding: ToolBinding,
    args: Dict[str, Any],
    *,
    principal_id: str,
    scope: Optional[str],
) -> ToolResult:
    """Dispatch an EPHEMERAL ``propose`` tool: run its service fn IMMEDIATELY.

    Only ephemeral conversation/session-state tools take this path (``set_focus``
    today). They are ``propose`` in the manifest's op-class taxonomy but write no
    substrate, so PC-8 (stage-substrate-mutations-for-bless) does not apply —
    there is nothing to bless — and a StagedChange would be a meaningless
    envelope. We call ``await fn(user_id=principal_id, **direct_param_map(args))``
    with the workspace scope bound via the ``current_scope_workspace_id``
    ContextVar (reset in ``finally`` so it cannot leak), exactly as a
    SERVICE-backed read does.

    Identity discipline (PC-1): ``user_id`` is ALWAYS the dispatch
    ``principal_id``, NEVER a tool arg; ``direct_param_map`` carries non-identity
    data only. Scope (PC-2): bound by the dispatcher, never an arg.

    Fail-closed: the ``direct_param_map`` may raise when a required arg the
    service needs is absent from the dispatch contract (e.g. ``set_focus`` needs
    a ``context_id`` that the external-MCP dispatch surface does not carry — see
    its mapper). A service that RETURNS a structured error (``{"error": ...}``)
    or ``None`` is normalized to an error ToolResult so a refusal never reads as
    a leaky success.
    """
    from app.services.agent_scope import current_scope_workspace_id

    fn = binding.direct_ref()  # type: ignore[misc]
    param_map = binding.direct_param_map or (lambda a: dict(a or {}))
    kwargs = param_map(args)

    token = current_scope_workspace_id.set(scope)
    try:
        data = await fn(user_id=principal_id, **kwargs)
    finally:
        current_scope_workspace_id.reset(token)

    if data is None:
        return ToolResult(
            is_error=True,
            error_code="not_found",
            message="direct dispatch returned no result",
        )
    if isinstance(data, dict) and data.get("error"):
        return ToolResult(
            is_error=True,
            error_code=str(data.get("error_code") or data.get("error") or "error"),
            message=str(data.get("message") or data.get("detail") or ""),
        )
    return ToolResult(data=data)


def _dispatch_execute_guard(spec: Any, args: Dict[str, Any]) -> ToolResult:
    """Guard the EXECUTE op-class behind an explicit write flag.

    There are 0 ``op_class == "execute"`` tools in the manifest today (every
    write currently lands as a ``propose`` that the user blesses), so this guard
    has no live tool to wire — execute tools are opt-in / out of scope for the
    existing surface. It is kept minimal and fail-closed: absent an explicit
    ``write=True`` (or ``confirm=True``) flag in the args, an execute dispatch is
    refused with ``write_scope_required`` rather than running a direct mutation.
    Wiring real execute tools (their own binding + guard semantics) is deferred.
    """
    if not (args.get("write") is True or args.get("confirm") is True):
        return ToolResult(
            is_error=True,
            error_code="write_scope_required",
            message=(
                f"{spec.name}: execute op_class requires an explicit write flag; "
                f"no execute tools are wired on this surface yet"
            ),
        )
    return ToolResult(
        is_error=True,
        error_code="not_implemented",
        message=f"{spec.name}: execute dispatch not wired (no execute tools)",
    )
