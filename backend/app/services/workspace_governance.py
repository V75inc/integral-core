"""Protected workspace stewardship, separate from ordinary collaborator roles.

This decision grants only the requested generic data/governance capability.
It does not create OWNS/COLLABORATES_ON edges or authorize an App business
action, personal credential export, or host execution.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator

from app.middleware.permissions_cache import isolated_permissions_cache
from app.models.nodes import Workspace
from app.services.resource_location import (
    DATA_APP_STATES,
    MANAGEMENT_APP_STATES,
    locate_resource,
)

OWNER_CAPABILITIES = frozenset(
    {
        "resource.read",
        "resource.write",
        "resource.configure",
        "resource.delete",
        "resource.access.review",
        "resource.access.manage",
        "resource.steward.transfer",
    }
)


_owner_governance_allowed: ContextVar[bool] = ContextVar(
    "integral_owner_governance_allowed", default=True
)


def owner_governance_allowed() -> bool:
    """Return the effective internal authorization mode for memo identity."""
    return _owner_governance_allowed.get()


@contextmanager
def ordinary_authority_only() -> Iterator[None]:
    """Evaluate App function authority without consuming protected stewardship."""
    token = _owner_governance_allowed.set(False)
    try:
        yield
    finally:
        _owner_governance_allowed.reset(token)


@dataclass(frozen=True)
class WorkspaceGovernanceDecision:
    principal_id: str
    workspace_id: str
    resource_type: str
    resource_id: str
    capability: str
    source: str = "workspace_owner"


async def resolve_owner_governance(
    user_id: str, resource_type: str, resource_id: str, capability: str
) -> WorkspaceGovernanceDecision | None:
    """Read live owner authority for one canonically contained resource."""
    if not _owner_governance_allowed.get():
        return None
    if capability not in OWNER_CAPABILITIES or resource_type not in {
        "workspace",
        "app",
        "track",
        "entry",
    }:
        return None
    from app.services.permissions import get_user_node
    from app.services.workspace_permissions import _current_workspace_role

    with isolated_permissions_cache():
        states = (
            DATA_APP_STATES
            if capability in {"resource.read", "resource.write"}
            else MANAGEMENT_APP_STATES
        )
        location = await locate_resource(
            resource_type, resource_id, allowed_app_states=states
        )
        if location is None:
            return None
        principal = await get_user_node(user_id)
        workspace = await Workspace.find_one({"id": location.workspace_id})
        if principal is None or workspace is None:
            return None
        if await _current_workspace_role(principal, workspace) != "owner":
            return None
        return WorkspaceGovernanceDecision(
            principal.id,
            workspace.id,
            resource_type,
            resource_id,
            capability,
        )
