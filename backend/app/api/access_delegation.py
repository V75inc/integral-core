"""Owner administration of flat groups and definition-bound action grants."""

from typing import Any

from fastapi import Request
from jvspatial.api import endpoint
from pydantic import ValidationError

from app.api.utils import resolve_principal_id
from app.schemas.access_delegation import (
    ActionBindingCreate,
    ActionDecisionRequest,
    EmptyDelegationMutation,
    GroupCreate,
)
from app.services import access_delegation as delegation
from app.services.request_scope import (
    require_effect_target_scope,
)


def _delegation_api_scope(function):
    """Honor every supplied workspace header against current canonical target."""
    import inspect
    from functools import wraps

    signature = inspect.signature(function)

    @wraps(function)
    async def invoke(*args: Any, **kwargs: Any) -> Any:
        values = signature.bind(*args, **kwargs)
        request = values.arguments["request"]
        actor = resolve_principal_id(request)
        if "app_id" in values.arguments:
            from app.api.errors import ResourceNotFoundError
            from app.services.resource_location import (
                MANAGEMENT_APP_STATES,
                locate_resource,
            )

            location = await locate_resource(
                "app",
                values.arguments["app_id"],
                allowed_app_states=MANAGEMENT_APP_STATES,
            )
            if location is None:
                raise ResourceNotFoundError(message="App not found")
            await require_effect_target_scope(request, actor, location.workspace_id)
        else:
            workspace_id = values.arguments.get("workspace_id")
            if "group_id" in values.arguments:
                workspace_id = await delegation.group_workspace(
                    values.arguments["group_id"]
                )
            elif "binding_id" in values.arguments:
                workspace_id = await delegation.binding_workspace(
                    values.arguments["binding_id"]
                )
            await require_effect_target_scope(request, actor, workspace_id)
        if request.method in {"PUT", "DELETE"}:
            try:
                EmptyDelegationMutation.model_validate(
                    await _json_body(request, allow_empty=True)
                )
            except ValidationError as exc:
                from app.api.errors import BadRequestError

                raise BadRequestError(
                    message="Invalid access delegation request"
                ) from exc
        try:
            return await function(*args, **kwargs)
        except ValidationError as exc:
            from app.api.errors import BadRequestError

            raise BadRequestError(message="Invalid access delegation request") from exc

    return invoke


async def _json_body(request: Request, *, allow_empty: bool = False) -> dict:
    from app.api.errors import BadRequestError

    if allow_empty and not await request.body():
        return {}
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequestError(message="Invalid JSON body") from exc
    if not isinstance(body, dict):
        raise BadRequestError(message="Request body must be a JSON object")
    return body


@endpoint(
    "/workspaces/{workspace_id}/access-groups",
    methods=["POST"],
    auth=True,
    tags=["Access"],
)
@_delegation_api_scope
async def create_access_group(request: Request, workspace_id: str) -> dict[str, Any]:
    """Create a rooted workspace group under current owner governance."""
    body = GroupCreate.model_validate(await _json_body(request))
    group = await delegation.create_group(
        actor_id=resolve_principal_id(request),
        workspace_id=workspace_id,
        name=body.name,
    )
    return await group.export(flat=True)


@endpoint(
    "/workspaces/{workspace_id}/access-groups",
    methods=["GET"],
    auth=True,
    tags=["Access"],
)
@_delegation_api_scope
async def list_access_groups(
    request: Request, workspace_id: str, cursor: str | None = None, limit: int = 50
) -> dict[str, Any]:
    """Owner-only paginated group inventory."""
    return await delegation.list_groups(
        actor_id=resolve_principal_id(request),
        workspace_id=workspace_id,
        cursor=cursor,
        limit=limit,
    )


@endpoint(
    "/access-groups/{group_id}/members/{user_id}",
    methods=["PUT"],
    auth=True,
    tags=["Access"],
)
@_delegation_api_scope
async def add_access_group_member(
    request: Request, group_id: str, user_id: str
) -> dict[str, bool]:
    """Bind current workspace membership lifetime to a flat group."""
    await delegation.set_group_member(
        actor_id=resolve_principal_id(request),
        group_id=group_id,
        user_id=user_id,
        member=True,
    )
    return {"member": True}


@endpoint(
    "/access-groups/{group_id}/members/{user_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Access"],
)
@_delegation_api_scope
async def remove_access_group_member(
    request: Request, group_id: str, user_id: str
) -> dict[str, bool]:
    """Remove group-derived action access without altering direct ACLs."""
    await delegation.set_group_member(
        actor_id=resolve_principal_id(request),
        group_id=group_id,
        user_id=user_id,
        member=False,
    )
    return {"member": False}


@endpoint(
    "/apps/{app_id}/action-bindings", methods=["POST"], auth=True, tags=["Access"]
)
@_delegation_api_scope
async def create_app_action_binding(request: Request, app_id: str) -> dict[str, Any]:
    """Bind an exact App function role to one user or workspace group."""
    body = ActionBindingCreate.model_validate(await _json_body(request))
    binding = await delegation.create_binding(
        actor_id=resolve_principal_id(request),
        app_id=app_id,
        subject_kind=body.subject_kind,
        subject_id=body.subject_id,
        role=body.role,
        conditions=[c.model_dump() for c in body.conditions],
        expires_at=body.expires_at,
    )
    return await binding.export(flat=True)


@endpoint("/apps/{app_id}/action-bindings", methods=["GET"], auth=True, tags=["Access"])
@_delegation_api_scope
async def list_app_action_bindings(
    request: Request, app_id: str, cursor: str | None = None, limit: int = 50
) -> dict[str, Any]:
    """List current and stale bindings for explicit owner review."""
    return await delegation.list_bindings(
        actor_id=resolve_principal_id(request),
        app_id=app_id,
        cursor=cursor,
        limit=limit,
    )


@endpoint(
    "/action-bindings/{binding_id}", methods=["DELETE"], auth=True, tags=["Access"]
)
@_delegation_api_scope
async def revoke_app_action_binding(
    request: Request, binding_id: str
) -> dict[str, bool]:
    """Revoke a specific action source under the workspace mutation fence."""
    await delegation.revoke_binding(
        actor_id=resolve_principal_id(request), binding_id=binding_id
    )
    return {"active": False}


@endpoint(
    "/access-groups/{group_id}/members", methods=["GET"], auth=True, tags=["Access"]
)
@_delegation_api_scope
async def list_access_group_members(
    request: Request, group_id: str, cursor: str | None = None, limit: int = 50
) -> dict[str, Any]:
    """Owner-only membership inventory including stale lifetime bindings."""
    return await delegation.list_group_members(
        actor_id=resolve_principal_id(request),
        group_id=group_id,
        cursor=cursor,
        limit=limit,
    )


@endpoint("/access-groups/{group_id}", methods=["DELETE"], auth=True, tags=["Access"])
@_delegation_api_scope
async def delete_access_group(request: Request, group_id: str) -> dict[str, bool]:
    """Remove a group and all of its dependent function authority."""
    await delegation.delete_group(
        actor_id=resolve_principal_id(request), group_id=group_id
    )
    return {"deleted": True}


@endpoint(
    "/apps/{app_id}/actions/{operation_key}/decision",
    methods=["POST"],
    auth=True,
    tags=["Access"],
)
@_delegation_api_scope
async def preview_app_action(
    request: Request, app_id: str, operation_key: str
) -> dict[str, Any]:
    """Return the caller's current target-bound decision without effects."""
    body = ActionDecisionRequest.model_validate(await _json_body(request))
    app, definition = await delegation.current_definition(app_id)
    payload = body.input
    from app.services.app_operations.registry import get_app_operation

    spec = get_app_operation(app["context"]["workspace_id"], app_id, operation_key)
    if spec and (spec.get("authorization") or {}).get("target_resolver"):
        from app.exceptions import AuthorizationUnavailableError
        from app.services.app_operations.dispatch import resolve_action_payload
        from app.services.hooks.tool_dispatch import validate_input

        if (
            spec.get("_definition_fingerprint")
            != definition["context"]["manifest_fingerprint"]
        ):
            raise AuthorizationUnavailableError()
        validate_input(payload, spec.get("input_schema") or {})
        payload = await resolve_action_payload(
            user_id=resolve_principal_id(request),
            workspace_id=app["context"]["workspace_id"],
            app_id=app_id,
            operation_key=operation_key,
            spec=spec,
            payload=payload,
        )
    decision = await delegation.action_decision(
        principal_id=resolve_principal_id(request),
        app_id=app_id,
        operation_key=operation_key,
        payload=payload,
    )
    return {"allowed": decision.allowed, "reason": decision.reason}
