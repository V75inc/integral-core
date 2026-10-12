"""Invoke typed app operations through a single Core dispatcher."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
from contextlib import nullcontext
from typing import Any, Dict, List, Optional

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    OperationIdempotencyConflictError,
    OperationReceiptRecoveryError,
    OperationTransactionUnavailableError,
    ResourceNotFoundError,
)
from app.contracts.runtime import ExecutionScope, InvalidExecutionScope
from app.models.nodes import App
from app.modules import core_modules
from app.schemas.policy import Resource, Subject
from app.services.app_operations.context import OperationContext
from app.services.app_operations.registry import (
    get_app_operation,
    list_registered_operations,
    register_app_operations,
    unregister_app_operations,
)
from app.services.hooks.registry import get_workspace_tools
from app.services.hooks.tool_dispatch import (
    resolve_handler,
    run_tool,
    validate_input,
    validate_output,
)
from app.services.permissions import resolve_role
from app.services.workspace_governance import ordinary_authority_call
from app.services.workspace_permissions import can_access_workspace

logger = logging.getLogger(__name__)

_READ_KINDS = frozenset({"read"})
_MUTATION_KINDS = frozenset({"propose", "execute", "destructive"})


async def policy_evaluate(
    *,
    subject: Subject,
    action: str,
    resource: Resource,
    execution_scope: ExecutionScope,
):
    """Compatibility adapter; policy ownership lives in ``app.modules``."""
    return await core_modules().policy.evaluate(
        scope=execution_scope,
        action=action,
        resource=resource,
    )


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
    from app.services.entitlements import require_installed_app_entitlement

    await require_installed_app_entitlement(app)


async def list_app_operations(
    *,
    user_id: str,
    workspace_id: str,
    app_id: str,
) -> Dict[str, Any]:
    """Return public metadata for declarations registered on an app."""
    try:
        execution_scope = ExecutionScope.create(
            principal_id=user_id,
            workspace_id=workspace_id,
            origin="app_declaration",
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
    await _ensure_app_active(app, user_id)
    ops = list_registered_operations(workspace_id, app_id)
    descriptors = []
    for spec in sorted(ops.values(), key=lambda operation: operation["key"]):
        descriptor = _public_operation(spec)
        if spec.get("authorization"):
            from app.services.access_delegation import (
                action_decision,
                current_definition,
            )

            _, definition = await current_definition(app_id)
            if spec.get("_definition_fingerprint") != definition["context"].get(
                "manifest_fingerprint"
            ):
                from app.exceptions import AuthorizationUnavailableError

                raise AuthorizationUnavailableError()
            decision = await action_decision(
                principal_id=user_id, app_id=app_id, operation_key=spec["key"]
            )
            descriptor.update(
                action=f"app:{app_id}:{spec['key']}",
                definition_id=definition["id"],
                allowed=decision.allowed,
                decision_reason=decision.reason,
            )
        descriptors.append(descriptor)
    return {"app_id": app_id, "operations": descriptors}


async def resolve_action_payload(
    *,
    user_id: str,
    workspace_id: str,
    app_id: str,
    operation_key: str,
    spec: dict,
    payload: dict,
) -> dict:
    """Resolve declared selectors read-only before exact current target authorization."""
    authorization = spec.get("authorization") or {}
    original_body = dict(payload)
    body = dict(original_body)
    if authorization.get("target_resolver"):
        from app.services.access_delegation import action_role_delegable

        if not await action_role_delegable(
            principal_id=user_id, app_id=app_id, operation_key=operation_key
        ):
            raise InsufficientPermissionsError(
                message="Current App function role required"
            )
        resolver_key = authorization["target_resolver"]
        resolver_spec = get_workspace_tools(workspace_id).get(resolver_key)
        if resolver_spec is None or resolver_spec.get("_bundle_slug") != spec.get(
            "_bundle_slug"
        ):
            raise BadRequestError(message="Operation target resolver unavailable")
        readonly_context = OperationContext(
            user_id=user_id,
            workspace_id=workspace_id,
            app_id=app_id,
            operation_key=operation_key,
            scope=f"operation:{app_id}:{operation_key}",
            bundle_slug=str(spec.get("_bundle_slug") or ""),
            read_only=True,
        )
        resolved = await run_tool(resolver_spec, original_body, readonly_context)
        declared_keys = {target["input"] for target in authorization.get("targets", [])}
        if (
            not isinstance(resolved, dict)
            or set(resolved) != declared_keys
            or any(type(value) is not str or not value for value in resolved.values())
        ):
            raise BadRequestError(message="Operation targets could not be resolved")
        body.update(resolved)
    return body


def operation_declaration_fingerprint(spec: Dict[str, Any], app: Any = None) -> str:
    """Bind a review to the registered contract, handler, and package identity."""
    return hashlib.sha256(
        json.dumps(
            {
                "operation": spec,
                "installed_identity": {
                    key: getattr(app, key, None)
                    for key in (
                        "installed_package_slug",
                        "installed_package_version",
                        "installed_artifact_fingerprint",
                        "active_definition_id",
                        "active_definition_revision",
                    )
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


async def _prepare_app_operation(
    *,
    user_id: str,
    workspace_id: str,
    app_id: str,
    operation_key: str,
    payload: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
    permit_deferred_decision: bool = False,
    subject: Optional[Subject] = None,
) -> Dict[str, Any]:
    """Read current declaration, inputs and authority without invoking its handler."""
    actor_subject = subject or Subject(kind="human", id=user_id)
    if actor_subject.kind not in {"human", "agent", "connector"}:
        raise InsufficientPermissionsError(message="Unsupported operation actor")
    try:
        execution_scope = ExecutionScope.create(
            principal_id=actor_subject.id,
            workspace_id=workspace_id,
            origin="app_operation",
        )
    except InvalidExecutionScope as exc:
        raise BadRequestError(message="no active workspace") from exc
    user_id = execution_scope.principal_id
    workspace_id = execution_scope.workspace_id
    key = str(operation_key or "").strip()
    if not key:
        raise BadRequestError(message="operation_key is required")

    app = await App.get(app_id)
    if app is None:
        raise ResourceNotFoundError(message="App not found")
    if getattr(app, "workspace_id", None) != workspace_id:
        raise InsufficientPermissionsError(message="App/workspace mismatch")

    if actor_subject.kind != "human":
        from app.services.app_operation_authority import operation_read_principal

        user_id = await operation_read_principal(
            subject=actor_subject,
            workspace_id=workspace_id,
            app_id=app_id,
            operation_key=key,
        )
    ws_role = await can_access_workspace(user_id, workspace_id)
    if ws_role == "none":
        raise InsufficientPermissionsError(message="Access denied")

    await _ensure_app_active(app, user_id)

    spec = get_app_operation(workspace_id, app_id, key)
    if spec is None:
        raise ResourceNotFoundError(message=f"operation {key!r} not found for app")

    spec = copy.deepcopy(spec)
    if spec.get("authorization"):
        from app.exceptions import AuthorizationUnavailableError
        from app.services.access_delegation import current_definition

        _, active_definition = await current_definition(app_id)
        if spec.get("_definition_fingerprint") != active_definition["context"].get(
            "manifest_fingerprint"
        ):
            raise AuthorizationUnavailableError()
    operation_kind = str(spec.get("kind") or "execute").strip().lower()
    if operation_kind not in _READ_KINDS | _MUTATION_KINDS:
        raise BadRequestError(message=f"unsupported operation kind {operation_kind!r}")
    original_body = copy.deepcopy(dict(payload or {}))
    validate_input(original_body, spec.get("input_schema") or {})
    body = await resolve_action_payload(
        user_id=user_id,
        workspace_id=workspace_id,
        app_id=app_id,
        operation_key=key,
        spec=spec,
        payload=original_body,
    )
    from app.services.approved_effect import validate_app_replay

    await validate_app_replay(
        subject=actor_subject,
        app_id=app_id,
        operation_key=key,
        workspace_id=workspace_id,
        original_input=original_body,
        resolved_input=body,
    )
    declared_policy_action = str(spec.get("policy_action") or "app.read").strip()
    policy_action = (
        f"app:{app_id}:{key}" if spec.get("authorization") else declared_policy_action
    )
    if operation_kind in _MUTATION_KINDS and policy_action == "app.read":
        policy_action = "entry.create"
    resource = Resource(kind="app", id=app_id, scope=f"app:{app_id}")
    if spec.get("authorization"):
        from app.services.app_operation_authority import operation_decision

        decision, current_author = await operation_decision(
            subject=actor_subject, app_id=app_id, operation_key=key, payload=body
        )
        if decision.allowed:
            user_id = current_author
    else:
        if actor_subject.kind != "human":
            raise InsufficientPermissionsError(
                message="Service operations require explicit function declarations"
            )
        decision = await policy_evaluate(
            subject=actor_subject,
            action=policy_action,
            resource=resource,
            execution_scope=execution_scope,
        )
    if not decision.allowed and not (
        permit_deferred_decision
        and spec.get("authorization")
        and actor_subject.kind in {"agent", "connector"}
        and decision.reason == "requires_human_approval"
    ):
        raise InsufficientPermissionsError(message="Access denied")
    policy_revision = core_modules().policy.revision(
        scope=execution_scope,
        action=policy_action,
        resource=resource,
        decision=decision,
    )

    return {
        "execution_scope": execution_scope,
        "actor_subject": actor_subject,
        "principal_id": user_id,
        "spec": spec,
        "body": copy.deepcopy(original_body),
        "resolved_body": copy.deepcopy(body),
        "key": key,
        "operation_kind": operation_kind,
        "policy_revision": policy_revision,
        "decision": decision,
        "app": app,
        "declaration_fingerprint": operation_declaration_fingerprint(spec, app),
    }


@ordinary_authority_call
async def prepare_app_operation(
    *,
    user_id: str,
    workspace_id: str,
    app_id: str,
    operation_key: str,
    payload: Optional[Dict[str, Any]] = None,
    subject: Optional[Subject] = None,
) -> Dict[str, Any]:
    """Authorize and resolve reviewed inputs without running an operation handler."""
    return await _prepare_app_operation(
        user_id=user_id,
        workspace_id=workspace_id,
        app_id=app_id,
        operation_key=operation_key,
        payload=payload,
        subject=subject,
    )


@ordinary_authority_call
async def invoke_app_operation(
    *,
    user_id: str,
    workspace_id: str,
    app_id: str,
    operation_key: str,
    payload: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
    correlation_id: Optional[str] = None,
    subject: Optional[Subject] = None,
    expected_declaration_fingerprint: Optional[str] = None,
    expected_operation_kind: Optional[str] = None,
    expected_resolved_input: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute under current actor authority and the exact reviewed declaration."""
    prepared = await _prepare_app_operation(
        user_id=user_id,
        workspace_id=workspace_id,
        app_id=app_id,
        operation_key=operation_key,
        payload=payload,
        subject=subject,
        idempotency_key=idempotency_key,
        permit_deferred_decision=True,
    )
    execution_scope = prepared["execution_scope"]
    workspace_id = execution_scope.workspace_id
    user_id, actor_subject = prepared["principal_id"], prepared["actor_subject"]
    spec, original_body, body = (
        prepared["spec"],
        prepared["body"],
        prepared["resolved_body"],
    )
    key, operation_kind = prepared["key"], prepared["operation_kind"]
    decision, app, policy_revision = (
        prepared["decision"],
        prepared["app"],
        prepared["policy_revision"],
    )
    if (
        expected_operation_kind is not None
        and operation_kind != expected_operation_kind
    ):
        raise BadRequestError(message="App operation execution class changed")
    if expected_declaration_fingerprint is not None and (
        (
            operation_kind != expected_operation_kind
            if expected_operation_kind
            else operation_kind not in _MUTATION_KINDS
        )
        or prepared["declaration_fingerprint"] != expected_declaration_fingerprint
    ):
        raise BadRequestError(
            message="App operation changed since review; prepare a new proposal",
            details={"error_code": "operation_declaration_changed"},
        )
    if expected_resolved_input is not None and body != expected_resolved_input:
        raise BadRequestError(
            message="App operation targets changed since review; prepare a new proposal",
            details={"error_code": "operation_targets_changed"},
        )
    if not decision.allowed:
        if (
            spec.get("authorization")
            and actor_subject.kind in {"agent", "connector"}
            and decision.reason == "requires_human_approval"
        ):
            from app.services.app_operation_approval import defer_app_operation

            if not str(idempotency_key or "").strip():
                raise BadRequestError(
                    message="idempotency_key is required for a mutating App operation"
                )
            return await defer_app_operation(
                subject=actor_subject,
                app_id=app_id,
                operation_key=key,
                workspace_id=workspace_id,
                payload=body,
                original_input=original_body,
                policy_id=decision.matched_policy_id,
                idempotency_key=str(idempotency_key).strip(),
            )
        raise InsufficientPermissionsError(message="Access denied")

    from app.services.app_operation_authority import operation_permit
    from app.services.app_operations.idempotency import (
        hash_request_payload,
        lookup_idempotent_result,
        store_idempotent_result,
    )
    from app.services.app_operations.result_access import authorize_replayed_result

    request_hash = hash_request_payload(original_body)
    idem_key = str(idempotency_key or "").strip()
    # Execution class, not a default policy action, determines whether a
    # declared capability can produce an effect.  A command that omitted its
    # policy action is normalized to ``entry.create`` above, but it must not
    # become an in-memory operation merely because the source default was
    # ``app.read``.  Pure handlers are declared ``kind: read`` instead.
    is_durable_command = operation_kind in _MUTATION_KINDS
    timeout_seconds = spec.get("timeout_seconds")
    if timeout_seconds is not None and is_durable_command:
        raise BadRequestError(
            message="timeouts are not supported for mutating operations",
            details={"error_code": "operation_timeout_unsupported"},
        )
    if timeout_seconds is not None:
        try:
            timeout_seconds = float(timeout_seconds)
        except (TypeError, ValueError) as exc:
            raise BadRequestError(message="invalid operation timeout") from exc
        if not 0 < timeout_seconds <= 300:
            raise BadRequestError(
                message="operation timeout must be between 0 and 300 seconds"
            )
    if idem_key and not is_durable_command:
        cached = await lookup_idempotent_result(
            workspace_id=workspace_id,
            app_id=app_id,
            operation_key=key,
            principal_id=actor_subject.id,
            idempotency_key=idem_key,
            request_hash=request_hash,
        )
        if cached is not None:
            await authorize_replayed_result(
                cached, user_id=user_id, workspace_id=workspace_id, app_id=app_id
            )
            return cached

    ctx = OperationContext(
        user_id=user_id,
        workspace_id=workspace_id,
        scope=f"operation:{app_id}:{key}",
        bundle_slug=str(spec.get("_bundle_slug") or ""),
        app_id=app_id,
        operation_key=key,
        actor_kind=actor_subject.kind,
        actor_id=actor_subject.id,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        deferred_change_events=[] if is_durable_command else None,
        read_only=operation_kind in _READ_KINDS,
    )

    tool_key = str(spec.get("tool") or "").strip()
    handler_ref = str(spec.get("handler_ref") or "").strip()

    async def run_handler() -> Dict[str, Any]:
        permit_scope = (
            operation_permit(
                principal_id=actor_subject.id,
                workspace_id=workspace_id,
                app_id=app_id,
                operation_key=key,
                payload=body,
                subject=actor_subject,
            )
            if is_durable_command or spec.get("authorization")
            else nullcontext()
        )
        async with permit_scope as bound_permit:
            # A definition can change after preparation while the receipt is
            # being admitted. Never enter an obsolete registered handler under
            # the replacement definition's freshly acquired permit.
            if (
                bound_permit is not None
                and spec.get("_definition_fingerprint")
                and bound_permit.fingerprint != spec["_definition_fingerprint"]
            ):
                raise BadRequestError(
                    message="App operation changed since review; prepare a new proposal",
                    details={"error_code": "operation_declaration_changed"},
                )
            if actor_subject.kind != "human":
                ctx.user_id = bound_permit.delegator_id

            async def invoke_handler():
                if tool_key:
                    tools = get_workspace_tools(workspace_id)
                    tool_spec = tools.get(tool_key)
                    if tool_spec is None:
                        raise ResourceNotFoundError(
                            message=f"operation {key!r} references missing tool {tool_key!r}"
                        )
                    return await run_tool(tool_spec, body, ctx)
                if handler_ref:
                    handler = resolve_handler(handler_ref)
                    return await handler(body, ctx)
                raise BadRequestError(
                    message=f"operation {key!r} has no handler_ref or tool",
                    details={"error_code": "operation_misconfigured"},
                )

            if timeout_seconds is None:
                output = await invoke_handler()
            else:
                try:
                    output = await asyncio.wait_for(
                        invoke_handler(), timeout=timeout_seconds
                    )
                except asyncio.TimeoutError as exc:
                    raise BadRequestError(
                        message=f"read operation {key!r} exceeded its timeout",
                        details={"error_code": "operation_timeout"},
                    ) from exc
            if not isinstance(output, dict):
                output = {"result": output}
            validate_output(output, spec.get("output_schema") or {})
            if spec.get("authorization"):
                from app.services.app_operation_authority import current_operation_actor

                if not await current_operation_actor(
                    subject=actor_subject,
                    workspace_id=workspace_id,
                    app_id=app_id,
                    operation_key=key,
                ):
                    raise InsufficientPermissionsError(
                        message="Current function authority required before result release"
                    )

        return await build_result(output)

    async def build_result(output: Dict[str, Any]) -> Dict[str, Any]:
        from app.models.edges import CONTAINS
        from app.models.nodes import App as AppNode
        from app.models.nodes import Entry, Track
        from app.schemas.capabilities import Evidence, ObjectRef
        from app.services.permissions import resolve_role
        from app.utils.time import utc_now_iso

        raw_refs = (output or {}).get("object_refs") or []
        if not isinstance(raw_refs, list):
            raise BadRequestError(message="operation object_refs must be a list")
        object_refs = []
        seen_refs = set()
        app_track_ids = set()
        if raw_refs:
            owning_app = await AppNode.get(app_id)
            app_tracks = (
                await owning_app.nodes(edge=[CONTAINS], node=["Track"], direction="out")
                if owning_app
                else []
            )
            app_track_ids = {str(item.id) for item in app_tracks}
        for raw_ref in raw_refs:
            ref = ObjectRef.model_validate(raw_ref)
            if ref.kind != "entry" or not ref.id or ref.id in seen_refs:
                raise BadRequestError(
                    message="operation object_refs must identify unique Entry nodes"
                )
            entry = await Entry.get(ref.id)
            track = (
                await Track.get(entry.track_id) if entry and entry.track_id else None
            )
            if (
                entry is None
                or track is None
                or str(track.id) not in app_track_ids
                or str(getattr(track, "workspace_id", "")) != workspace_id
                or await resolve_role(user_id, "entry", entry.id) is None
            ):
                raise BadRequestError(
                    message="operation object_ref is outside the active App scope"
                )
            seen_refs.add(ref.id)
            object_refs.append(
                ObjectRef(
                    kind="entry",
                    id=str(entry.id),
                    workspace_id=workspace_id,
                    app_id=app_id,
                    title=str(getattr(entry, "title", "") or "") or None,
                ).model_dump()
            )

        evidence = Evidence(
            object_refs=[ObjectRef.model_validate(r) for r in object_refs],
            freshness=utc_now_iso(),
            applied_scope=f"ws:{workspace_id}",
            policy_decision_id=getattr(decision, "decision_id", None)
            or getattr(decision, "id", None),
            policy_revision=policy_revision,
            audit_correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            package_version=str(getattr(app, "installed_package_version", "") or "")
            or None,
            package_slug=str(getattr(app, "installed_package_slug", "") or "") or None,
        ).model_dump()

        return {
            "app_id": app_id,
            "operation_key": key,
            "output": output,
            "object_refs": object_refs,
            "evidence": evidence,
        }

    if is_durable_command:
        if not idem_key:
            raise BadRequestError(
                message="idempotency_key is required for a mutating App operation",
                details={"error_code": "operation_idempotency_required"},
            )
        from app.contracts.operations import OperationIdentity
        from app.services.app_operations.event_outbox import (
            deliver_operation_event,
            event_outbox_id,
        )
        from app.services.app_operations.execution_receipts import (
            OperationReceiptConflict,
            OperationReceiptIncomplete,
            execute_operation_once,
            receipt_reference,
        )
        from app.services.app_operations.transaction_scope import (
            OperationTransactionUnavailable,
        )

        identity = OperationIdentity.create(
            workspace_id=workspace_id,
            app_id=app_id,
            operation_key=key,
            principal_id=actor_subject.id,
            idempotency_key=idem_key,
        )
        try:
            execution = await execute_operation_once(
                identity=identity,
                request_hash=request_hash,
                execute=run_handler,
                event_outbox=ctx.deferred_change_events,
            )
        except OperationReceiptConflict as exc:
            raise OperationIdempotencyConflictError(message=str(exc)) from exc
        except OperationReceiptIncomplete as exc:
            raise OperationReceiptRecoveryError() from exc
        except OperationTransactionUnavailable as exc:
            raise OperationTransactionUnavailableError() from exc
        if not execution.replayed:
            for sequence, _event in enumerate(ctx.deferred_change_events or []):
                from app.services.approved_effect import (
                    defer_approved_operation_delivery,
                )

                outbox_id = event_outbox_id(identity, sequence)
                if not await defer_approved_operation_delivery(outbox_id):
                    await deliver_operation_event(outbox_id=outbox_id)
        else:
            await authorize_replayed_result(
                execution.result,
                user_id=user_id,
                workspace_id=workspace_id,
                app_id=app_id,
            )
        return {
            **execution.result,
            "operation_receipt": receipt_reference(
                identity, replayed=execution.replayed
            ),
        }

    result = await run_handler()
    if idem_key:
        await store_idempotent_result(
            workspace_id=workspace_id,
            app_id=app_id,
            operation_key=key,
            principal_id=actor_subject.id,
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
        if item.get("authorization"):
            from app.services.application_definitions import definition_fingerprint

            item["_definition_fingerprint"] = definition_fingerprint(canonical)
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
