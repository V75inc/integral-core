"""Rooted owned-App fixtures for operation transport and schema contracts."""

from app.models.edges import IS_MEMBER_OF, OWNS
from app.models.nodes import App, User, Workspace
from app.services.app_graph import (
    catalog_app,
    catalog_user,
    catalog_workspace,
    ensure_integral_app_graph,
)
from app.services.hooks.registry import clear_workspace_registrations
from app.services.permissions import get_user_node


async def owned_operation_app(auth_user, request):
    """Bind an actual authenticated profile and private App to a rooted workspace."""
    await ensure_integral_app_graph(include_library=False)
    profile = await get_user_node(auth_user.id)
    if profile is None:
        profile = await User.create(
            user_id=auth_user.id, display_name="Operation contract owner"
        )
        await catalog_user(profile)
    workspace = await Workspace.create(name="Operation contract", kind="organization")
    await catalog_workspace(workspace)
    await profile.connect(workspace, edge=IS_MEMBER_OF, role="owner")
    app = await App.create(name="Reference operations", workspace_id=workspace.id)
    await catalog_app(app)
    await profile.connect(app, edge=OWNS)
    request.addfinalizer(lambda: clear_workspace_registrations(workspace.id))
    return workspace, app
