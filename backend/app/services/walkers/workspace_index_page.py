"""Resumable rooted workspace discovery with a constant-depth keyset frontier."""

from typing import Any

from jvspatial.core import Node, Walker
from jvspatial.core.decorators import on_visit

from app.models.edges import CATALOGS, CONTAINS
from app.models.nodes import Entry, Track, Tracks, Workspace
from app.services.app_graph import _workspace_branch_id


class WorkspaceIndexPageWalker(Walker):
    """Walk Workspace → Tracks registry → Track → Entry in bounded slices.

    The registry includes App and top-level Tracks. Every page uses graph
    membership; stale scalar workspace caches cannot omit a canonical source.
    A depth-three frontier survives restarts without retaining source text or
    the workspace's complete Track/Entry inventory.
    """

    workspace_id: str
    frames: list[dict[str, Any]] = []
    entries: list[Entry] = []
    page_size: int = 100
    steps: int = 0

    async def resume(self):
        if not self.frames:
            self.frames = [{"id": self.workspace_id, "cursor": None}]
        while self.frames:
            start = await Node.get(self.frames[-1]["id"])
            if start is not None:
                self.steps = 1
                await self.spawn(start)
                break
            self.frames.pop()
        return self.entries, self.frames

    async def _next(self):
        while self.frames and self.frames[-1].get("done"):
            self.frames.pop()
        if not self.frames or self.steps >= 16 or len(self.entries) >= self.page_size:
            return
        self.steps += 1
        here = await Node.get(self.frames[-1]["id"])
        if here is None:
            self.frames.pop()
            await self._next()
        else:
            await self.visit(here)

    @on_visit(Workspace)
    async def on_workspace(self, here: Workspace):
        if here.id != self.workspace_id:
            raise ValueError("Invalid workspace index frontier")
        branches, _ = await here.nodes_page(
            edge=[CONTAINS],
            node=[{"Tracks": {"id": _workspace_branch_id(here.id, "tracks")}}],
            limit=2,
        )
        self.frames.pop()
        if branches:
            self.frames.append({"id": branches[0].id, "cursor": None})
        await self._next()

    @on_visit(Tracks)
    async def on_registry(self, here: Tracks):
        if here.id != _workspace_branch_id(self.workspace_id, "tracks"):
            raise ValueError("Invalid workspace index registry")
        tracks, cursor = await here.nodes_page(
            edge=[CATALOGS], node=[Track], cursor=self.frames[-1]["cursor"], limit=1
        )
        self.frames[-1].update(cursor=cursor, done=cursor is None)
        if tracks:
            self.frames.append({"id": tracks[0].id, "cursor": None})
        await self._next()

    @on_visit(Track)
    async def on_track(self, here: Track):
        entries, cursor = await here.nodes_page(
            edge=[CONTAINS],
            node=[Entry],
            cursor=self.frames[-1]["cursor"],
            limit=self.page_size - len(self.entries),
        )
        self.frames[-1].update(cursor=cursor, done=cursor is None)
        self.entries.extend(entries)
        await self._next()
