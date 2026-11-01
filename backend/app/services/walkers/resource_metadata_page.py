"""One rooted App/Track registry page for repairable metadata indexing."""

from typing import Literal

from jvspatial.core import Walker
from jvspatial.core.decorators import on_visit

from app.models.edges import CATALOGS, CONTAINS
from app.models.nodes import App, Apps, Track, Tracks, Workspace
from app.services.app_graph import _workspace_branch_id


class ResourceMetadataPageWalker(Walker):
    workspace_id: str
    kind: Literal["apps", "tracks"]
    cursor: str | None = None
    next_cursor: str | None = None
    source_ids: list[str] = []

    @on_visit(Workspace)
    async def on_workspace(self, here: Workspace):
        if here.id != self.workspace_id:
            raise ValueError("Metadata workspace mismatch")
        branch = Apps if self.kind == "apps" else Tracks
        registries = await here.nodes(
            edge=[CONTAINS], node=[branch], direction="out", limit=2
        )
        expected = _workspace_branch_id(here.id, self.kind)
        await self.visit(
            [registry for registry in registries if registry.id == expected]
        )

    @on_visit(Apps, Tracks)
    async def on_registry(self, here):
        if here.id != _workspace_branch_id(self.workspace_id, self.kind):
            raise ValueError("Metadata registry mismatch")
        rows, self.next_cursor = await here.nodes_page(
            edge=[CATALOGS],
            node=[App if self.kind == "apps" else Track],
            cursor=self.cursor,
            limit=100,
        )
        self.source_ids = [row.id for row in rows]
