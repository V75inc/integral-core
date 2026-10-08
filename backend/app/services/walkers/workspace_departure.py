"""Collect a user's grants through the resource graph before membership removal."""

from typing import Any, Dict, List

from jvspatial.core import Walker
from jvspatial.core.decorators import on_visit

from app.models.edges import COLLABORATES_ON, CONTAINS, EXCLUDED_FROM
from app.models.nodes import App, Entry, Track, User


class WorkspaceDepartureWalker(Walker):
    """Follow member grants and Entry parents without changing other workspaces."""

    workspace_id: str
    resources: Dict[str, Any] = {}

    @on_visit(User)
    async def on_user(self, here: User) -> None:
        targets = await here.nodes(
            edge=[COLLABORATES_ON, EXCLUDED_FROM],
            node=["WorkspaceApp", "Track", "Entry"],
            direction="out",
        )
        await self.visit(targets)

    @on_visit(App, Track)
    async def on_resource(self, here: Any) -> None:
        if here.workspace_id == self.workspace_id:
            self.resources[here.id] = here

    @on_visit(Entry)
    async def on_entry(self, here: Entry) -> None:
        parents = await here.nodes(edge=[CONTAINS], node=["Track"], direction="in")
        if any(track.workspace_id == self.workspace_id for track in parents):
            self.resources[here.id] = here


async def workspace_departure_grant_targets(user: User, workspace_id: str) -> List[Any]:
    """Return in-workspace grant targets; graph failures propagate to the caller."""
    walker = WorkspaceDepartureWalker(workspace_id=workspace_id)
    await walker.spawn(user)
    report = await walker.get_report()
    # jvspatial reports hook exceptions instead of raising them. A partial
    # inventory must never authorize a successful membership removal.
    if any(isinstance(item, dict) and item.get("hook_error") for item in report):
        raise RuntimeError("Workspace grant traversal failed")
    return list(walker.resources.values())
