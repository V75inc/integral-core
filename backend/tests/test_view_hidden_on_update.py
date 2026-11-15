"""An App can retire a tab on update (manifest ``hidden: true``) without deleting it.

Update from library used to set ``hidden`` only when it created a view, so a
view the App later marked hidden stayed visible in existing workspaces. The
merge now remembers what the manifest last said and changes ``hidden`` only
when that changes, so a person's own show or hide choice survives updates.
"""

from __future__ import annotations

import copy

import pytest

from app.models.edges import CONTAINS
from app.models.nodes import App
from app.services.app_lifecycle import install_app, update_app_from_library
from app.services.view_scope import list_scoped_track_views
from tests.fixtures.workspaces import workspace_owner_id
from tests.test_app_lifecycle import (
    _make_library_cp,
    _make_workspace,
    _minimal_app_manifest,
)


def _with_views(manifest, *, version, extra_hidden):
    m = copy.deepcopy(manifest)
    m["package"]["version"] = version
    m["app"]["tracks"][0]["views"] = [
        {"key": "feed", "name": "Feed", "type": "feed"},
        {"key": "old_tab", "name": "Old tab", "type": "table", "hidden": extra_hidden},
    ]
    return m


async def _views_by_key(app_id):
    app = await App.get(app_id)
    tracks = await app.nodes(edge=[CONTAINS], node=["Track"])
    track = next(t for t in tracks if t.title == "Demo Track")
    return {
        str((v.config or {}).get("_manifest_view_key") or ""): v
        for v in await list_scoped_track_views(track.id)
    }


@pytest.mark.asyncio
async def test_update_retires_a_tab_and_keeps_a_persons_later_choice():
    ws = await _make_workspace()
    actor = await workspace_owner_id(ws)
    base = _minimal_app_manifest(package_name="view-retire", version="1.0.0")
    lib = await _make_library_cp(_with_views(base, version="1.0.0", extra_hidden=False))
    installed = await install_app(
        workspace_id=ws.id,
        library_cp_id=lib.id,
        actor_id=actor,
        include_seed_data=False,
    )
    app_id = installed["app_id"]
    views = await _views_by_key(app_id)
    assert views["old_tab"].hidden is False

    # 2.0.0 retires the tab: the update hides it (the record is kept).
    lib.manifest = _with_views(base, version="2.0.0", extra_hidden=True)
    lib.version = "2.0.0"
    await lib.save()
    await update_app_from_library(app_id=app_id, actor_id=actor)
    views = await _views_by_key(app_id)
    assert views["old_tab"].hidden is True
    assert views["feed"].hidden is False

    # A person shows it again; a later update with the same manifest leaves it.
    old_tab = views["old_tab"]
    old_tab.hidden = False
    await old_tab.save()
    lib.manifest = _with_views(base, version="2.0.1", extra_hidden=True)
    lib.version = "2.0.1"
    await lib.save()
    await update_app_from_library(app_id=app_id, actor_id=actor)
    views = await _views_by_key(app_id)
    assert views["old_tab"].hidden is False

    # The App brings it back: the update shows it (already shown, stays shown).
    lib.manifest = _with_views(base, version="3.0.0", extra_hidden=False)
    lib.version = "3.0.0"
    await lib.save()
    await update_app_from_library(app_id=app_id, actor_id=actor)
    views = await _views_by_key(app_id)
    assert views["old_tab"].hidden is False
    assert (views["old_tab"].config or {}).get("_manifest_hidden") is False


def test_view_config_keeps_the_manifest_hidden_marker():
    from app.services.operational_model_compile import normalize_view_config

    assert (
        normalize_view_config("table", {"_manifest_hidden": True})["_manifest_hidden"]
        is True
    )
    assert "_manifest_hidden" not in normalize_view_config("table", {})
