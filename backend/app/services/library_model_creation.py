"""Create private library Models with named workspace ownership."""

from typing import Any

from jvspatial.core.context import get_default_context

from app.api.errors import InsufficientPermissionsError
from app.exceptions import AuthorizationUnavailableError
from app.middleware.permissions_cache import isolated_permissions_cache
from app.models.edges import HAS_LIBRARY_MODEL
from app.models.nodes import (
    OPERATIONAL_MODELS_REGISTRY_ID,
    OperationalModel,
    OperationalModels,
    Workspace,
)
from app.services.model_authority import ModelAuthorityWalker, resolve_model_authority


async def _create_private_model(
    user_id: str, workspace_id: str, fields: dict
) -> OperationalModel:
    from app.services.app_graph import ensure_catalog_edge
    from app.services.permissions import (
        can_publish_operational_models_under_workspace,
        get_user_node,
    )
    from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource
    from app.services.workspace_governance import owner_governance_allowed
    from app.services.workspace_permissions import (
        can_access_workspace,
        member_creation_flags,
    )

    with isolated_permissions_cache():
        user = await get_user_node(user_id)
        role = await can_access_workspace(user_id, workspace_id)
        if user is None or role not in {"owner", "admin", "member", "guest"}:
            raise InsufficientPermissionsError(message="Access denied")
        if not await can_publish_operational_models_under_workspace(
            user_id, workspace_id
        ):
            raise InsufficientPermissionsError(message="Access denied")
        if (
            role == "owner"
            and not owner_governance_allowed()
            and not any(await member_creation_flags(user, workspace_id))
        ):
            raise InsufficientPermissionsError(message="Access denied")
        if (
            await locate_resource(
                "workspace", workspace_id, allowed_app_states=MANAGEMENT_APP_STATES
            )
            is None
        ):
            raise AuthorizationUnavailableError()
        context = get_default_context()
        walker = ModelAuthorityWalker()
        if not await walker.check_library_catalog(context):
            raise AuthorizationUnavailableError()
        workspace = await walker.read_node(Workspace, workspace_id, context)
        registry = await walker.read_node(
            OperationalModels, OPERATIONAL_MODELS_REGISTRY_ID, context
        )
        model = await OperationalModel.create(
            **fields, workspace_id=workspace_id, library_package=True
        )
        try:
            await workspace.connect(model, edge=HAS_LIBRARY_MODEL)
            await ensure_catalog_edge(registry, model)
            authority = await resolve_model_authority(
                model.id, allowed_app_states=MANAGEMENT_APP_STATES
            )
            if (
                authority is None
                or authority.platform
                or authority.workspace_id != workspace_id
            ):
                raise AuthorizationUnavailableError()
        except BaseException:
            await model.delete()
            raise
        return model


async def create_private_library_model(
    *, user_id: str, workspace_id: str, **fields: Any
) -> OperationalModel:
    """Join Model, workspace ownership and catalog attachment in one store unit.

    PostgreSQL joins the current graph transaction. Nontransactional development
    adapters use cleanup and carry no atomicity claim. Creation flags authorize
    creation; they do not create an unrestricted configuration grant.
    """
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
        postgres_graph_transaction,
    )

    if not workspace_id or any(
        key in fields
        for key in ("workspace_id", "library_package", "app_id", "draft_of_id")
    ):
        raise ValueError("Private library creation requires exact workspace ownership")
    if graph_transaction_available():
        async with postgres_graph_transaction():
            model = await _create_private_model(user_id, workspace_id, fields)
        await model.set_context(get_default_context())
        return model
    return await _create_private_model(user_id, workspace_id, fields)
