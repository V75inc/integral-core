"""Invoke typed app operations through a single Core dispatcher."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceNotFoundError,
)
from app.models.nodes import App
from app.schemas.policy import Resource, Subject
from app.services.app_operations.context import OperationContext
from app.services.app_operations.registry import (
    get_app_operation,
    list_registered_operations,
    register_app_operations,
    unregister_app_operations,
)
from app.services.hooks.registry import get_workspace_tools
from app.services.hooks.tool_dispatch import resolve_handler, run_tool, validate_input
from app.services.permissions import resolve_role
from app.services.policy_engine import evaluate as policy_evaluate
from app.services.workspace_permissions import can_access_workspace

logger = logging.getLogger(__name__)

_READ_KINDS = frozenset({"read"})
_MUTATION_KINDS = frozenset({"propose", "execute", "destructive"})


def _public_operation(spec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "key": spec.get("key"),
        "kind": spec.get("kind"),
        "name": spec.get("name"),
        "description": spec.get("description") or "",
        "policy_action": spec.get("policy_action"),
        "capability": spec.get("capability"),
        "staging_level": spec.get("staging_level"),
        "idempotency_key": spec.get("idempotency_key"),
        "timeout_seconds": spec.get("timeout_seconds"),
        "input_schema": spec.get("input_schema") or {},
        "output_schema": spec.get("output_schema") or {},
    }


async def _ensure_app_active(app: App, user_id: str) -> None:
    state = str(getattr(app, "lifecycle_state", "active") or "active")
    if state not in ("active", "awaiting_settings"):
        raise BadRequestError(
            message=f"App is not active (state={state})",
            details={"error_code": "app_not_active"},
        )
    role = await resolve_role(user_id, "app", app.id)
    if role is None:
        raise InsufficientPermissionsError(message="Access denied")


async def list_app_operations(
    *,
    user_id: str,
    workspace_id: str,
    app_id: str,
) -> Dict[str, Any]:
    """Return public metadata for operations registered on an app."""
    app = await App.get(app_id)
    if app is None:
        raise ResourceNotFoundError(message="App not found")
    if getattr(app, "workspace_id", None) != workspace_id:
        raise InsufficientPermissionsError(message="App/workspace mismatch")
    await _ensure_app_active(app, user_id)
    ops = list_registered_operations(workspace_id, app_id)
    return {
        "app_id": app_id,
        "operations": [
            _public_operation(s) for s in sorted(ops.values(), key=lambda o: o["key"])
        ],
    }


async def invoke_app_operation(
    *,
    user_id: str,
    workspace_id: str,
    app_id: str,
    operation_key: str,
    payload: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a typed operation with policy, validation, and idempotency."""
    if not workspace_id:
        raise BadRequestError(message="no active workspace")
    key = str(operation_key or "").strip()
    if not key:
        raise BadRequestError(message="operation_key is required")

    app = await App.get(app_id)
    if app is None:
        raise ResourceNotFoundError(message="App not found")
    if getattr(app, "workspace_id", None) != workspace_id:
        raise InsufficientPermissionsError(message="App/workspace mismatch")

    ws_role = await can_access_workspace(user_id, workspace_id)
    if ws_role == "none":
        raise InsufficientPermissionsError(message="Access denied")

    await _ensure_app_active(app, user_id)

    spec = get_app_operation(workspace_id, app_id, key)
    if spec is None:
        raise ResourceNotFoundError(message=f"operation {key!r} not found for app")

    policy_action = str(spec.get("policy_action") or "app.read").strip()
    if spec.get("kind") in _MUTATION_KINDS and policy_action == "app.read":
        policy_action = "entry.create"
    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action=policy_action,
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    body = dict(payload or {})
    validate_input(body, spec.get("input_schema") or {})

    from app.services.app_invariant_guards import (
        reset_operation_write_active,
        set_operation_write_active,
    )
    from app.services.app_operations.idempotency import (
        hash_request_payload,
        lookup_idempotent_result,
        store_idempotent_result,
    )

    request_hash = hash_request_payload(body)
    idem_key = str(idempotency_key or "").strip()
    if idem_key:
        cached = await lookup_idempotent_result(
            workspace_id=workspace_id,
            app_id=app_id,
            operation_key=key,
            principal_id=user_id,
            idempotency_key=idem_key,
            request_hash=request_hash,
        )
        if cached is not None:
            return cached

    ctx = OperationContext(
        user_id=user_id,
        workspace_id=workspace_id,
        scope=f"operation:{app_id}:{key}",
        bundle_slug=str(spec.get("_bundle_slug") or ""),
        app_id=app_id,
        operation_key=key,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    )

    tool_key = str(spec.get("tool") or "").strip()
    handler_ref = str(spec.get("handler_ref") or "").strip()

    bypass_token = set_operation_write_active(True)
    try:
        if tool_key:
            tools = get_workspace_tools(workspace_id)
            tool_spec = tools.get(tool_key)
            if tool_spec is None:
                raise ResourceNotFoundError(
                    message=f"operation {key!r} references missing tool {tool_key!r}"
                )
            output = await run_tool(tool_spec, body, ctx)
        elif handler_ref:
            handler = resolve_handler(handler_ref)
            output = await handler(body, ctx)
            if not isinstance(output, dict):
                output = {"result": output}
        else:
            raise BadRequestError(
                message=f"operation {key!r} has no handler_ref or tool",
                details={"error_code": "operation_misconfigured"},
            )
    finally:
        reset_operation_write_active(bypass_token)

    from app.schemas.capabilities import Evidence, ObjectRef
    from app.utils.time import utc_now_iso

    object_refs = []
    for candidate_key in ("asset", "custody", "entry"):
        node = (output or {}).get(candidate_key)
        if isinstance(node, dict) and node.get("entry_id"):
            object_refs.append(
                ObjectRef(
                    kind="entry",
                    id=str(node["entry_id"]),
                    workspace_id=workspace_id,
                    app_id=app_id,
                    title=node.get("title"),
                ).model_dump()
            )
        elif isinstance(node, dict) and node.get("id"):
            object_refs.append(
                ObjectRef(
                    kind="entry",
                    id=str(node["id"]),
                    workspace_id=workspace_id,
                    app_id=app_id,
                    title=node.get("title"),
                ).model_dump()
            )
    for id_key in ("asset_id", "entry_id", "custody_id"):
        eid = (output or {}).get(id_key)
        if eid:
            object_refs.append(
                ObjectRef(
                    kind="entry",
                    id=str(eid),
                    workspace_id=workspace_id,
                    app_id=app_id,
                ).model_dump()
            )

    evidence = Evidence(
        object_refs=[ObjectRef.model_validate(r) for r in object_refs],
        freshness=utc_now_iso(),
        applied_scope=f"ws:{workspace_id}",
        policy_decision_id=getattr(decision, "decision_id", None)
        or getattr(decision, "id", None),
        audit_correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        package_version=str(getattr(app, "installed_package_version", "") or "")
        or None,
        package_slug=str(getattr(app, "installed_package_slug", "") or "") or None,
    ).model_dump()

    result = {
        "app_id": app_id,
        "operation_key": key,
        "output": output,
        "object_refs": object_refs,
        "evidence": evidence,
    }
    if idem_key:
        await store_idempotent_result(
            workspace_id=workspace_id,
            app_id=app_id,
            operation_key=key,
            principal_id=user_id,
            idempotency_key=idem_key,
            request_hash=request_hash,
            result=result,
        )
    return result


def operations_from_canonical(
    canonical: Dict[str, Any],
    *,
    bundle_slug: str,
    bundle_dir: str | None,
) -> List[Dict[str, Any]]:
    """Normalize manifest operations for registration."""
    from app.services.hooks.install_hook import _normalize_handler_ref

    raw = list((canonical.get("app") or {}).get("operations") or [])
    out: List[Dict[str, Any]] = []
    for op in raw:
        item = dict(op)
        ref = str(item.get("handler_ref") or "").strip()
        if ref:
            item["handler_ref"] = _normalize_handler_ref(
                bundle_slug, ref, bundle_dir=bundle_dir
            )
        out.append(item)
    return out


async def sync_app_operations_from_manifest(
    *,
    workspace_id: str,
    app_id: str,
    canonical: Dict[str, Any],
    bundle_slug: str,
    bundle_dir: str | None = None,
) -> None:
    """Replace operation registrations from a compiled app manifest."""
    ops = operations_from_canonical(
        canonical, bundle_slug=bundle_slug, bundle_dir=bundle_dir
    )
    unregister_app_operations(workspace_id, app_id)
    register_app_operations(workspace_id, app_id, bundle_slug, ops)
