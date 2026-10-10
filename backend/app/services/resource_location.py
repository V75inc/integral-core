"""Canonical bounded resource containment for execution scope gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jvspatial.core import Edge, Walker
from jvspatial.core.decorators import on_visit
from jvspatial.core.entities import Root

from app.exceptions import AuthorizationUnavailableError
from app.models.edges import CATALOGS, CONTAINS
from app.models.nodes import (
    APP_NODE_ID,
    WORKSPACES_REGISTRY_ID,
    App,
    Apps,
    Entry,
    IntegralApp,
    Track,
    Tracks,
    Workspace,
    Workspaces,
)


@dataclass(frozen=True)
class ResourceLocation:
    workspace_id: str
    app_id: str = ""
    track_id: str = ""


class ResourceLocationWalker(Walker):
    """Resolve one bounded structural parent chain and reject inconsistent facts."""

    workspace_id: str = ""
    app_id: str = ""
    track_id: str = ""
    valid: bool = True

    async def check_workspace_root(self, workspace_id: str, context: Any) -> bool:
        """Verify the named Root-to-Workspace catalog path from current facts."""
        for model, node_id in (
            (Root, "n.Root.root"),
            (IntegralApp, APP_NODE_ID),
            (Workspaces, WORKSPACES_REGISTRY_ID),
        ):
            if await model.find_one({"id": node_id}) is None:
                return False
        # all() cannot consume the async generator suggested by SIM111.
        for source, target, edge in (  # noqa: SIM111
            ("n.Root.root", APP_NODE_ID, Edge),
            (APP_NODE_ID, WORKSPACES_REGISTRY_ID, Edge),
            (WORKSPACES_REGISTRY_ID, workspace_id, CATALOGS),
        ):
            if not await context.find_edges_between(source, target, edge_class=edge):
                return False
        return True

    @on_visit(Workspace)
    async def on_workspace(self, here: Workspace) -> None:
        if not await self.check_workspace_root(here.id, await here.get_context()):
            self.valid = False
            return
        self.workspace_id = here.id

    async def check_workspace_catalog(self, node: Any, kind: str) -> bool:
        from app.services.app_graph import _workspace_branch_id

        workspace_id = str(getattr(node, "workspace_id", "") or "").strip()
        if not workspace_id or (
            self.workspace_id and self.workspace_id != workspace_id
        ):
            return False
        workspace = await Workspace.find_one({"id": workspace_id})
        registry_class = Apps if kind == "apps" else Tracks
        branch_id = _workspace_branch_id(workspace_id, kind)
        branch = await registry_class.find_one({"id": branch_id})
        if workspace is None or branch is None or branch.workspace_id != workspace_id:
            return False
        context = await node.get_context()
        if not await self.check_workspace_root(workspace_id, context):
            return False
        if not await context.find_edges_between(
            workspace_id, branch_id, edge_class=CONTAINS
        ):
            return False
        registries = await node.nodes(
            edge=[CATALOGS], node=[registry_class], direction="in", limit=2
        )
        if len(registries) != 1 or registries[0].id != branch_id:
            return False
        workspace_parents = await node.nodes(
            edge=[CONTAINS], node=[Workspace], direction="in", limit=2
        )
        if len(workspace_parents) > 1 or any(
            parent.id != workspace_id for parent in workspace_parents
        ):
            return False
        if not await context.find_edges_between(
            branch_id, node.id, edge_class=CATALOGS
        ):
            return False
        self.workspace_id = workspace_id
        return True

    @on_visit(Entry)
    async def on_entry(self, here: Entry) -> None:
        parents = await here.nodes(
            edge=[CONTAINS], direction="in", node=["Track"], limit=2
        )
        if len(parents) != 1 or (here.track_id and here.track_id != parents[0].id):
            self.valid = False
            return
        await self.visit(parents)

    @on_visit(Track)
    async def on_track(self, here: Track) -> None:
        here = await Track.find_one({"id": here.id})
        if here is None:
            self.valid = False
            return
        if not await self.check_workspace_catalog(here, "tracks"):
            self.valid = False
            return
        self.track_id = here.id
        parents = await here.nodes(
            edge=[CONTAINS], direction="in", node=["WorkspaceApp"], limit=2
        )
        if len(parents) > 1:
            self.valid = False
            return
        if parents:
            await self.visit(parents)

    @on_visit(App)
    async def on_app(self, here: App) -> None:
        here = await App.find_one({"id": here.id})
        if here is None:
            self.valid = False
            return
        if not await self.check_workspace_catalog(here, "apps"):
            self.valid = False
            return
        if str(getattr(here, "lifecycle_state", "active") or "") != "active":
            self.valid = False
            return
        self.app_id = here.id


async def locate_resource(kind: str, resource_id: str) -> ResourceLocation | None:
    """Resolve live canonical scope; deny corrupt facts and surface read outages."""
    model = {"entry": Entry, "track": Track, "app": App, "workspace": Workspace}.get(
        kind
    )
    if model is None or not resource_id:
        return None
    try:
        node = await model.find_one({"id": resource_id})
        if node is None:
            return None
        walker = ResourceLocationWalker()
        await walker.spawn(node)
        if any(
            isinstance(report, dict) and "hook_error" in report
            for report in await walker.get_report()
        ):
            raise AuthorizationUnavailableError()
        if not walker.valid or not walker.workspace_id:
            return None
        return ResourceLocation(walker.workspace_id, walker.app_id, walker.track_id)
    except Exception as exc:
        raise AuthorizationUnavailableError() from exc
