"""Workspace scope policy for platform and private library packages."""

from typing import Optional

from jvspatial.api.exceptions import ResourceNotFoundError

from app.models.nodes import OperationalModel


async def visible_library_model(
    model_id: str,
    workspace_id: Optional[str],
    *,
    user_id: Optional[str] = None,
    include_drafts: bool = False,
) -> Optional[OperationalModel]:
    """Capture and admit a library row using current named publication/ownership."""
    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.schemas.policy import Resource, Subject
    from app.services.model_authority import get_current_model, resolve_model_authority
    from app.services.policy_engine import evaluate

    with isolated_permissions_cache():
        if user_id and workspace_id:
            from app.services.workspace_permissions import can_access_workspace

            if await can_access_workspace(user_id, workspace_id) not in {
                "owner",
                "admin",
                "member",
                "guest",
            }:
                return None
        model = await get_current_model(model_id)
        if model is None or not model.library_package:
            return None
        authority = await resolve_model_authority(model_id)
        if authority is None or (authority.unpublished and not include_drafts):
            return None
        if not authority.platform and (
            not workspace_id or authority.workspace_id != workspace_id
        ):
            return None
        if user_id is None:
            return model if authority.platform and not authority.unpublished else None
        decision = await evaluate(
            subject=Subject(kind="human", id=user_id),
            action="operational_model.read",
            resource=Resource(
                kind="operational_model",
                id=model_id,
                scope=f"operational_model:{model_id}",
            ),
        )
        return model if decision.allowed else None


async def assert_library_package_installable_in_workspace(
    library_cp: OperationalModel,
    target_workspace_id: str,
    *,
    user_id: Optional[str] = None,
) -> OperationalModel:
    """Return a current admitted source; scalar ownership cannot authorize it."""
    current = await visible_library_model(
        library_cp.id, target_workspace_id, user_id=user_id
    )
    if current is None:
        raise ResourceNotFoundError(message="Operational Model not found")
    return current
