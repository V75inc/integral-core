"""Workspace scope policy for platform and private library packages."""

from typing import Optional

from jvspatial.api.exceptions import ResourceNotFoundError

from app.models.nodes import OperationalModel


def library_package_owner_workspace_id(profile: OperationalModel) -> Optional[str]:
    """Workspace that owns a private library package, or None for platform/seeded."""
    wid = str(getattr(profile, "workspace_id", "") or "").strip()
    return wid or None


def is_library_package_visible_for_workspace(
    profile: OperationalModel, active_workspace_id: Optional[str]
) -> bool:
    """Platform packages are global; private packages only for their workspace."""
    pkg_ws = library_package_owner_workspace_id(profile)
    if not pkg_ws:
        return True
    return bool(active_workspace_id) and pkg_ws == active_workspace_id


async def assert_library_package_installable_in_workspace(
    library_cp: OperationalModel, target_workspace_id: str
) -> None:
    """Reject installing another workspace's private template into this one."""
    pkg_ws = library_package_owner_workspace_id(library_cp)
    if pkg_ws and pkg_ws != target_workspace_id:
        raise ResourceNotFoundError(message="Operational Model not found")
