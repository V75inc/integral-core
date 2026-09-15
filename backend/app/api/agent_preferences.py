"""Per-user × per-workspace agent preference endpoints.

Stores the user's active agent for a given workspace as a
``HasAgentPreference`` edge from ``User → Workspace``. The thread record
carries the agent at creation time (Task 5); this preference is the
default for *new* threads, not a binding on existing ones.

Idempotent — at most one edge per (user, workspace) pair, enforced via
``services/edge_upsert.ensure_edge``. Latest write wins; ``updated_at``
is refreshed on every set.
"""

from datetime import datetime, timezone

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import BadRequestError, ResourceNotFoundError
from app.api.utils import resolve_principal_id
from app.models.edges import HasAgentPreference
from app.models.nodes import Workspace
from app.schemas.agent_preferences import (
    AgentPreferenceResponse,
    AgentPreferenceValue,
)
from app.services.chat_providers import get_registry
from app.services.edge_upsert import ensure_edge
from app.services.permissions import get_user_node
from app.services.workspace_permissions import can_access_workspace


async def _load_user_and_workspace(request: Request, workspace_id: str):
    """Resolve the caller + workspace, enforcing access.

    Returns ``(user_node, workspace_node)``. Raises
    ``ResourceNotFoundError`` if the workspace doesn't exist or the
    caller has no access (we don't disclose existence on access denial).
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise ResourceNotFoundError(message="Workspace not found")
    user = await get_user_node(user_id)
    if user is None:
        raise ResourceNotFoundError(message="Workspace not found")
    workspace = await Workspace.get(workspace_id)
    if workspace is None:
        raise ResourceNotFoundError(message="Workspace not found")
    role = await can_access_workspace(user_id, workspace_id)
    if role == "none":
        raise ResourceNotFoundError(message="Workspace not found")
    return user, workspace


@endpoint(
    "/workspaces/{workspace_id}/agent-preference",
    methods=["GET"],
    auth=True,
    tags=["AI Chat"],
)
async def get_agent_preference(
    request: Request, workspace_id: str
) -> AgentPreferenceResponse:
    """Return the caller's active agent for the given workspace.

    Returns ``{"preference": null}`` when no preference has been set —
    callers distinguish unset from a stored ``null`` selection that way.
    """
    user, workspace = await _load_user_and_workspace(request, workspace_id)
    ctx = await user.get_context()
    edges = await ctx.find_edges_between(
        user.id, workspace.id, edge_class=HasAgentPreference
    )
    if not edges:
        return AgentPreferenceResponse(preference=None)
    edge = edges[0]
    return AgentPreferenceResponse(
        preference=AgentPreferenceValue(
            provider_id=edge.provider_id, agent_id=edge.agent_id
        )
    )


@endpoint(
    "/workspaces/{workspace_id}/agent-preference",
    methods=["PUT"],
    auth=True,
    tags=["AI Chat"],
)
async def put_agent_preference(
    request: Request,
    workspace_id: str,
    provider_id: str = "",
    agent_id: str = "",
) -> AgentPreferenceResponse:
    """Set the caller's active agent for the given workspace.

    Idempotent — latest write wins. Rejects unknown ``provider_id``
    with 400 via the chat-provider registry.
    """
    if not provider_id:
        raise BadRequestError(message="provider_id is required")
    if not agent_id:
        raise BadRequestError(message="agent_id is required")

    user, workspace = await _load_user_and_workspace(request, workspace_id)

    if get_registry().get(provider_id) is None:
        raise BadRequestError(message=f"Unknown provider_id: {provider_id}")

    now_iso = datetime.now(timezone.utc).isoformat()
    edge = await ensure_edge(
        user,
        workspace,
        HasAgentPreference,
        provider_id=provider_id,
        agent_id=agent_id,
        updated_at=now_iso,
    )
    # ensure_edge returns the existing edge if one was present — refresh
    # fields so latest-write-wins semantics hold even when the (user,
    # workspace) pair already had a preference recorded.
    edge.provider_id = provider_id
    edge.agent_id = agent_id
    edge.updated_at = now_iso
    await edge.save()

    return AgentPreferenceResponse(
        preference=AgentPreferenceValue(provider_id=provider_id, agent_id=agent_id)
    )
