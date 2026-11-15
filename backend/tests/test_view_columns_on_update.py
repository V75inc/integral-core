"""Compatible package updates preserve saved columns and advance untouched defaults."""

from copy import deepcopy

import pytest

from app.models.edges import CONTAINS
from app.models.nodes import App, View
from app.services.app_lifecycle import install_app, update_app_from_library
from app.services.view_scope import list_scoped_track_views
from tests.fixtures.workspaces import workspace_owner_id
from tests.test_app_lifecycle import (
    _make_library_cp,
    _make_workspace,
    _minimal_app_manifest,
)

pytestmark = pytest.mark.smoke


def _with_columns(manifest, version, columns):
    manifest = deepcopy(manifest)
    manifest["package"]["version"] = version
    manifest["app"]["tracks"][0]["views"] = [
        {
            "key": key,
            "name": key.title(),
            "view_type": "table",
            "columns": deepcopy(columns),
        }
        for key in ("customized", "untouched")
    ]
    return manifest


@pytest.mark.asyncio
async def test_populated_view_upgrade_preserves_custom_columns_and_can_follow_defaults_again():
    workspace = await _make_workspace()
    actor = await workspace_owner_id(workspace)
    manifest = _minimal_app_manifest(package_name="view-columns", version="1.0.0")
    first_columns = [{"field": "title", "label": "Title"}]
    library = await _make_library_cp(_with_columns(manifest, "1.0.0", first_columns))
    installed = await install_app(
        workspace_id=workspace.id,
        library_cp_id=library.id,
        actor_id=actor,
        include_seed_data=False,
    )
    app = await App.get(installed["app_id"])
    tracks = await app.nodes(edge=[CONTAINS], node=["Track"])
    assert len(tracks) == 1
    views = {
        view.config["_manifest_view_key"]: view
        for view in await list_scoped_track_views(tracks[0].id)
    }
    customized = views["customized"]
    customized.config = {
        **customized.config,
        "columns": [{"field": "title", "label": "My title"}],
        "tab_order": 8,
    }
    await customized.save()
    before = deepcopy(customized.config)
    added_columns = [*first_columns, {"field": "body", "label": "Notes"}]
    library.manifest = _with_columns(manifest, "1.0.1", added_columns)
    library.version = "1.0.1"
    await library.save()
    await update_app_from_library(app_id=app.id, actor_id=actor)
    await update_app_from_library(app_id=app.id, actor_id=actor)
    assert (await View.get(customized.id)).config == before
    untouched = await View.get(views["untouched"].id)
    assert untouched.config["columns"] == added_columns
    assert {view.id for view in await list_scoped_track_views(tracks[0].id)} == {
        view.id for view in views.values()
    }
    assert {track.id for track in await app.nodes(edge=[CONTAINS], node=["Track"])} == {
        tracks[0].id
    }

    # Choosing the package's current columns again opts back into its defaults;
    # no permanent override bit or destructive reset is needed.
    customized = await View.get(customized.id)
    customized.config = {**customized.config, "columns": deepcopy(added_columns)}
    await customized.save()
    next_columns = [*added_columns, {"field": "created_at", "label": "Created"}]
    library.manifest = _with_columns(manifest, "1.0.2", next_columns)
    library.version = "1.0.2"
    await library.save()
    await update_app_from_library(app_id=app.id, actor_id=actor)
    assert (await View.get(customized.id)).config["columns"] == next_columns
    assert (await View.get(customized.id)).config["tab_order"] == 8
