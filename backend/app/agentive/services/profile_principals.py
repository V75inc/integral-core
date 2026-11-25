"""Workspace-rooted, non-login ACL subjects for published profiles."""

from uuid import uuid4

from app.api.errors import BadRequestError
from app.models.edges import HAS_PROFILE_PRINCIPAL, IS_MEMBER_OF
from app.models.nodes import PROFILE_USER_PREFIX, User, Workspace, is_profile_user_id
from app.services.membership_write_scope import membership_write_scope


async def current_principal(config, key, item):
    """Resolve the exact published profile’s assigned service user."""
    principal_id = item.get("service_principal_id")
    if not isinstance(principal_id, str) or not is_profile_user_id(principal_id):
        return None
    principal = await User.find_one({"id": principal_id})
    if (
        not principal
        or principal.user_id
        or principal.service_workspace_id != config.workspace_id
        or principal.service_profile_key != key
    ):
        return None
    ctx = await config.get_context()
    edges = await ctx.find_edges_between(
        config.id, principal.id, edge_class=HAS_PROFILE_PRINCIPAL
    )
    return principal if len(edges) == 1 and edges[0].profile_key == key else None


async def ensure_principal(config, key, item):
    """Create or refresh the member under the common authorization fence."""
    async with membership_write_scope(config.workspace_id):
        await _ensure_principal(config, key, item)


async def _ensure_principal(config, key, item):
    name = item["published"]["name"]
    if "service_principal_id" in item:
        principal = await current_principal(config, key, item)
        if principal is None:
            raise BadRequestError("Published profile identity needs repair")
        if principal.display_name != name:
            principal.display_name = name
            await principal.save()
        return
    ctx = await config.get_context()
    existing = await ctx.database.find(
        "edge",
        {
            "source": config.id,
            "entity": HAS_PROFILE_PRINCIPAL.__name__,
            "context.profile_key": key,
        },
        limit=1,
    )
    if existing:
        raise BadRequestError("Published profile identity needs repair")
    workspace = await Workspace.find_one({"id": config.workspace_id})
    if workspace is None:
        raise BadRequestError("Workspace unavailable")
    principal = await User.create(
        id=f"{PROFILE_USER_PREFIX}{uuid4().hex}",
        display_name=name,
        service_workspace_id=workspace.id,
        service_profile_key=key,
    )
    await config.connect(principal, edge=HAS_PROFILE_PRINCIPAL, profile_key=key)
    # Guests receive no workspace-visibility cascade. Access changes and removals
    # use the normal membership and resource ACLs and survive republication.
    await principal.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    item["service_principal_id"] = principal.id
