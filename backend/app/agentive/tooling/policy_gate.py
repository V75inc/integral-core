"""Manifest policy_action enforcement at the tooling dispatch boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from app.contracts.runtime import ExecutionScope, InvalidExecutionScope
from app.modules import policy_module
from app.schemas.policy import Resource, ResourceKind, Subject

if TYPE_CHECKING:
    from .dispatch import ToolResult

# Arg keys that identify a single resource for policy evaluation.
# ``conflict_id`` / ``context_id`` are omitted — not members of
# :data:`ResourceKind`; those tools defer to handler-side gates.
_ARG_TO_KIND: Tuple[Tuple[str, ResourceKind], ...] = (
    ("entry_id", "entry"),
    ("track_id", "track"),
    ("app_id", "app"),
    ("view_id", "view"),
    ("tag_id", "tag"),
    ("connector_id", "connector"),
    ("notification_id", "notification"),
    ("operational_model_id", "operational_model"),
    ("operational_model_id", "operational_model"),
)

# ``resource_type`` template args may use manifest aliases; map to ResourceKind.
_RESOURCE_TYPE_ALIASES: Dict[str, ResourceKind] = {
    "entry": "entry",
    "track": "track",
    "app": "app",
    "operational_model": "operational_model",
    "tag": "tag",
    "view": "view",
    "connector": "connector",
    "notification": "notification",
    "user": "user",
    "organization": "organization",
}

# Actions that apply to a *collection* of resources; the handler filters
# per-item (permissions.get_user_accessible_entries, retrieve I-RET-01, etc.).
# A dispatch-time gate on track_id/app_id would misfire when the agent passes
# a track NAME (``"projects"``) instead of ``n.Track.*`` — defer to handler.
_COLLECTION_ENTRY_ACTIONS = frozenset(
    {
        "entry.read",
    }
)

# Policy resource.kind → persisted Object id prefix. Agents often pass human
# names/slugs; services resolve those before point checks (agent_insights,
# activity_digest). Dispatch must not evaluate policy on unresolved ids.
_KIND_ID_PREFIX: Dict[ResourceKind, str] = {
    "entry": "n.Entry.",
    "track": "n.Track.",
    "app": "n.WorkspaceApp.",  # App node __entity_name__ == "WorkspaceApp"
    "view": "n.View.",
    "operational_model": "n.OperationalModel.",
}


async def policy_evaluate(
    *,
    subject: Subject,
    action: str,
    resource: Resource,
    execution_scope: ExecutionScope,
):
    """Compatibility adapter; policy ownership lives in ``app.modules``."""
    return await policy_module.evaluate(
        scope=execution_scope, action=action, resource=resource
    )


def _coerce_resource_kind(raw: str) -> Optional[ResourceKind]:
    """Map a manifest ``resource_type`` string to a policy :class:`ResourceKind`."""
    return _RESOURCE_TYPE_ALIASES.get(raw.lower())


def _resolve_policy_action(action: str, args: Dict[str, Any]) -> Optional[str]:
    """Expand templated manifest actions like ``{resource_type}.read``."""
    if "{resource_type}" not in action:
        return action
    resource_type = args.get("resource_type")
    if not resource_type:
        return None
    return action.replace("{resource_type}", str(resource_type))


def _action_kind(action: str) -> Optional[str]:
    if "." not in action:
        return None
    return action.split(".", 1)[0]


def _resolve_resource(args: Dict[str, Any]) -> Optional[Resource]:
    """Best-effort resource extraction from tool args for policy checks."""
    resource_type = args.get("resource_type")
    resource_id = args.get("resource_id")
    if resource_type and resource_id:
        kind = _coerce_resource_kind(str(resource_type))
        if kind is None:
            return None
        rid = str(resource_id)
        return Resource(kind=kind, id=rid, scope=f"{kind}:{rid}")

    for arg_key, kind in _ARG_TO_KIND:
        value = args.get(arg_key)
        if value:
            rid = str(value)
            return Resource(kind=kind, id=rid, scope=f"{kind}:{rid}")
    return None


def _should_defer_to_handler(
    action: str, args: Dict[str, Any], resource: Resource
) -> bool:
    """True when a dispatch-time check would be wrong or redundant."""
    action_kind = _action_kind(action)
    if action_kind is None:
        return True

    # Collection reads (query_entries, retrieve, list endpoints): handler
    # already permission-filters every returned row.
    if action in _COLLECTION_ENTRY_ACTIONS and not args.get("entry_id"):
        return True

    # Action/resource kind mismatch (e.g. entry.read resolved via track_id).
    if action_kind != resource.kind:
        return True

    # Point checks need a real Object id — agents often pass names/slugs;
    # handlers resolve before fetching (agent_insights.query_entries, etc.).
    expected_prefix = _KIND_ID_PREFIX.get(resource.kind)
    if expected_prefix and not resource.id.startswith(expected_prefix):
        return True

    return False


async def enforce_tool_policy(
    spec: Any,
    args: Dict[str, Any],
    *,
    principal_id: str,
    workspace_id: Optional[str] = None,
) -> Optional["ToolResult"]:
    """Evaluate manifest ``policy_action`` for point reads/writes only.

    Returns an error :class:`ToolResult` when denied. Returns ``None`` when
    allowed or when the handler must filter (collection queries, track-name
    args, kind mismatches).

    ``propose`` tools defer entirely: dispatch only mints a StagedChange; the
    bless/executor path (and route handlers) run the authoritative policy gate
    on the resolved resource id.
    """
    if getattr(spec, "op_class", None) == "propose":
        return None

    raw_action = spec.policy_action
    if not raw_action:
        return None

    action = _resolve_policy_action(str(raw_action), args or {})
    if not action:
        return None

    resource = _resolve_resource(args or {})
    if resource is None:
        return None

    if _should_defer_to_handler(action, args or {}, resource):
        return None

    try:
        execution_scope = ExecutionScope.create(  # deviation: value-contract constructor, not a graph mutation
            principal_id=principal_id,
            workspace_id=workspace_id or "",
            origin="resident_tool",
        )
    except InvalidExecutionScope:
        from app.agentive.tooling.dispatch import ToolResult

        return ToolResult(
            is_error=True,
            error_code="invalid_execution_scope",
            message="A bound workspace is required to evaluate this tool",
        )

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=execution_scope.principal_id),
        action=action,  # type: ignore[arg-type]
        resource=resource,
        execution_scope=execution_scope,
    )
    if decision.allowed:
        return None
    from app.agentive.tooling.dispatch import ToolResult

    return ToolResult(
        is_error=True,
        error_code="forbidden",
        message=f"Policy denied {action}",
    )


def sanitize_tool_args(
    tool_name: str,
    args: Dict[str, Any],
    *,
    scope: Optional[str],
) -> Dict[str, Any]:
    """Strip scope-widening args and bind workspace scope for retrieval tools."""
    out = dict(args or {})

    # PC-2: agent dispatch must not widen workspace via cross_workspace.
    out.pop("cross_workspace", None)

    retrieve_tools = {"integral_query", "integral_search_cross_track"}
    if tool_name in retrieve_tools and scope:
        body_scope = out.get("scope")
        if not body_scope:
            out["scope"] = f"workspace:{scope}"
        elif isinstance(body_scope, str) and body_scope.startswith("workspace:"):
            requested_ws = body_scope.split(":", 1)[1]
            if requested_ws != scope:
                out["_scope_violation"] = True

    return out
