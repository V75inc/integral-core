"""Declared App query dispatch (ADR-012)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceNotFoundError,
)
from app.contracts.runtime import ExecutionScope, InvalidExecutionScope
from app.models.nodes import App
from app.modules import policy_module
from app.schemas.policy import Resource, Subject
from app.services.app_operations.context import OperationContext
from app.services.app_queries.registry import (
    get_app_query,
    list_registered_queries,
    register_app_queries,
    unregister_app_queries,
)
from app.services.hooks.registry import get_workspace_tools
from app.services.hooks.tool_dispatch import resolve_handler, run_tool, validate_input
from app.services.permissions import resolve_role
from app.services.workspace_permissions import can_access_workspace


async def policy_evaluate(
    *,
    subject: Subject,
    action: str,
    resource: Resource,
    execution_scope: ExecutionScope,
):
    """Compatibility adapter; policy ownership lives in ``app.modules``."""
    return await policy_module.evaluate(
        scope=execution_scope,
        action=action,
        resource=resource,
    )


def _public_query(spec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "key": spec.get("key"),
        "name": spec.get("name"),
        "description": spec.get("description") or "",
        "policy_action": spec.get("policy_action"),
        "input_schema": spec.get("input_schema") or {},
        "output_schema": spec.get("output_schema") or {},
    }


async def list_app_queries(
    *,
    user_id: str,
    workspace_id: str,
    app_id: str,
) -> Dict[str, Any]:
    """Return public metadata for declarations registered on an app."""
    try:
        execution_scope = ExecutionScope.create(
            principal_id=user_id, workspace_id=workspace_id, origin="app_declaration"
        )
    except InvalidExecutionScope as exc:
        raise BadRequestError(message="no active workspace") from exc
    user_id = execution_scope.principal_id
    workspace_id = execution_scope.workspace_id
    app = await App.get(app_id)
    if app is None:
        raise ResourceNotFoundError(message="App not found")
    if getattr(app, "workspace_id", None) != workspace_id:
        raise InsufficientPermissionsError(message="App/workspace mismatch")
    state = str(getattr(app, "lifecycle_state", "active") or "active")
    if state not in ("active", "awaiting_settings"):
        raise BadRequestError(
            message=f"App is not active (state={state})",
            details={"error_code": "app_not_active"},
        )
    if await resolve_role(user_id, "app", app_id) is None:
        raise InsufficientPermissionsError(message="Access denied")
    qs = list_registered_queries(workspace_id, app_id)
    return {
        "app_id": app_id,
        "queries": [
            _public_query(s) for s in sorted(qs.values(), key=lambda o: o["key"])
        ],
    }


async def invoke_app_query(
    *,
    user_id: str,
    workspace_id: str,
    app_id: str,
    query_key: str,
    params: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a declared App query with policy and input validation."""
    try:
        execution_scope = ExecutionScope.create(
            principal_id=user_id, workspace_id=workspace_id, origin="app_query"
        )
    except InvalidExecutionScope as exc:
        raise BadRequestError(message="no active workspace") from exc
    user_id = execution_scope.principal_id
    workspace_id = execution_scope.workspace_id
    key = str(query_key or "").strip()
    if not key:
        raise BadRequestError(message="query_key is required")

    app = await App.get(app_id)
    if app is None:
        raise ResourceNotFoundError(message="App not found")
    if getattr(app, "workspace_id", None) != workspace_id:
        raise InsufficientPermissionsError(message="App/workspace mismatch")

    ws_role = await can_access_workspace(user_id, workspace_id)
    if ws_role == "none":
        raise InsufficientPermissionsError(message="Access denied")

    state = str(getattr(app, "lifecycle_state", "active") or "active")
    if state not in ("active", "awaiting_settings"):
        raise BadRequestError(
            message=f"App is not active (state={state})",
            details={"error_code": "app_not_active"},
        )
    if await resolve_role(user_id, "app", app_id) is None:
        raise InsufficientPermissionsError(message="Access denied")

    spec = get_app_query(workspace_id, app_id, key)
    if spec is None:
        raise ResourceNotFoundError(message=f"query {key!r} not found for app")

    policy_action = str(spec.get("policy_action") or "app.read").strip()
    resource = Resource(kind="app", id=app_id, scope=f"app:{app_id}")
    decision = await policy_evaluate(
        subject=Subject(kind="human", id=execution_scope.principal_id),
        action=policy_action,
        resource=resource,
        execution_scope=execution_scope,
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    policy_revision = policy_module.revision(
        scope=execution_scope,
        action=policy_action,
        resource=resource,
        decision=decision,
    )

    body = dict(params or {})
    validate_input(body, spec.get("input_schema") or {})

    ctx = OperationContext(
        user_id=user_id,
        workspace_id=workspace_id,
        scope=f"query:{app_id}:{key}",
        bundle_slug=str(spec.get("_bundle_slug") or ""),
        app_id=app_id,
        operation_key=key,
        idempotency_key=None,
        correlation_id=correlation_id,
    )

    tool_key = str(spec.get("tool") or "").strip()
    handler_ref = str(spec.get("handler_ref") or "").strip()
    if tool_key:
        tools = get_workspace_tools(workspace_id)
        tool_spec = tools.get(tool_key)
        if tool_spec is None:
            raise ResourceNotFoundError(
                message=f"query {key!r} references missing tool {tool_key!r}"
            )
        output = await run_tool(tool_spec, body, ctx)
    elif handler_ref:
        handler = resolve_handler(handler_ref)
        output = await handler(body, ctx)
        if not isinstance(output, dict):
            output = {"result": output}
    else:
        raise BadRequestError(
            message=f"query {key!r} has no handler_ref or tool",
            details={"error_code": "query_misconfigured"},
        )

    return {
        "app_id": app_id,
        "query_key": key,
        "output": output,
        "policy_decision_id": getattr(decision, "decision_id", None)
        or getattr(decision, "id", None),
        "policy_revision": policy_revision,
    }


def queries_from_canonical(
    canonical: Dict[str, Any],
    *,
    bundle_slug: str,
    bundle_dir: str | None,
) -> List[Dict[str, Any]]:
    """Normalize query declarations from a compiled App manifest."""
    from app.services.hooks.install_hook import _normalize_handler_ref

    raw = list((canonical.get("app") or {}).get("queries") or [])
    out: List[Dict[str, Any]] = []
    for q in raw:
        item = dict(q)
        ref = str(item.get("handler_ref") or "").strip()
        if ref:
            item["handler_ref"] = _normalize_handler_ref(
                bundle_slug, ref, bundle_dir=bundle_dir
            )
        out.append(item)
    return out


async def sync_app_queries_from_manifest(
    *,
    workspace_id: str,
    app_id: str,
    canonical: Dict[str, Any],
    bundle_slug: str,
    bundle_dir: str | None = None,
) -> None:
    """Replace in-process query registrations from the App's canonical manifest."""
    qs = queries_from_canonical(
        canonical, bundle_slug=bundle_slug, bundle_dir=bundle_dir
    )
    unregister_app_queries(workspace_id, app_id)
    register_app_queries(workspace_id, app_id, bundle_slug, qs)
