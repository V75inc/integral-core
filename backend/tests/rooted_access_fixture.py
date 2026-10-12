"""Explicit normal permission fixtures; corrupt-state tests build their own graphs."""

from dataclasses import dataclass
from typing import Any

import pytest

from app.models.edges import CONTAINS, IS_MEMBER_OF
from app.models.nodes import App, Entry, Track, User, Workspace
from app.services.app_graph import (
    catalog_app,
    catalog_track,
    catalog_user,
    catalog_workspace,
)


@dataclass
class RootedAccessGraph:
    workspace_node: Workspace

    async def user(self, **fields: Any) -> User:
        user = await User.create(**fields)
        await catalog_user(user)
        await user.connect(self.workspace_node, edge=IS_MEMBER_OF, role="member")
        return user

    async def workspace(self, **fields: Any) -> Workspace:
        workspace = await Workspace.create(**fields)
        await catalog_workspace(workspace)
        return workspace

    async def app(self, **fields: Any) -> App:
        fields.setdefault("workspace_id", self.workspace_node.id)
        app = await App.create(**fields)
        await catalog_app(app)
        return app

    async def track(self, **fields: Any) -> Track:
        fields.setdefault("workspace_id", self.workspace_node.id)
        track = await Track.create(**fields)
        await catalog_track(track)
        return track

    async def entry(self, **fields: Any) -> Entry:
        track = await Track.find_one({"id": fields["track_id"]})
        assert track is not None
        entry = await Entry.create(**fields)
        await track.connect(entry, edge=CONTAINS)
        return entry


@pytest.fixture
async def rooted_access_graph():
    owner = await User.create(display_name="Independent fixture workspace owner")
    await catalog_user(owner)
    workspace = await Workspace.create(name="Permission fixture", kind="organization")
    await catalog_workspace(workspace)
    await owner.connect(workspace, edge=IS_MEMBER_OF, role="owner")
    return RootedAccessGraph(workspace)
