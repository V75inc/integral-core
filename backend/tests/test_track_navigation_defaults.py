"""Page-local navigation sharing facts preserve exact defaults and fresh scope."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.edges import CONTAINS, HAS_OPERATIONAL_MODEL, OWNS
from app.models.nodes import OperationalModel, Track, Workspace
from app.services.app_graph import (
    catalog_track,
    create_track_view,
    get_track_attached_operational_model,
)
from app.services.entry_context import (
    attach_operational_model_defaults_to_track_data,
    attach_operational_model_defaults_to_tracks_data,
)
from app.services.operational_model_graph import (
    shared_track_profile_ids_for_navigation,
)
from app.services.permissions import get_user_node
from app.services.view_scope import list_scoped_track_views

pytestmark = pytest.mark.asyncio


def _profile(key, parents=1, *, template=False):
    return SimpleNamespace(
        id=key,
        manifest=(
            {"materialization": {"kind": "space_track_template"}} if template else {}
        ),
        count_nodes=AsyncMock(return_value=parents),
    )


def _projected_database(monkeypatch, rows):
    database = SimpleNamespace(find_projected=AsyncMock(return_value=rows))
    monkeypatch.setattr(
        "jvspatial.core.context.get_default_context",
        lambda: SimpleNamespace(database=database),
    )
    return database


@pytest.mark.unit
async def test_dedicated_shared_and_template_classification_match_exact_counts(
    monkeypatch,
):
    dedicated = _profile("dedicated")
    shared = _profile("shared", 2)
    template = _profile("template", 0, template=True)
    database = _projected_database(
        monkeypatch,
        [{"target": "dedicated"}, {"target": "shared"}, {"target": "shared"}],
    )

    assert await shared_track_profile_ids_for_navigation(
        [dedicated, shared, template, shared]
    ) == {"shared", "template"}
    dedicated.count_nodes.assert_not_awaited()
    shared.count_nodes.assert_awaited_once()
    template.count_nodes.assert_not_awaited()
    call = database.find_projected.await_args
    assert call.kwargs == {"fields": ["target"], "limit": 5}
    assert set(call.args[1]["target"]["$in"]) == {"dedicated", "shared"}


@pytest.mark.unit
@pytest.mark.parametrize("row_count", [5, 6], ids=["at-limit", "over-limit"])
async def test_overflow_counts_profiles_missing_from_the_projected_page(
    monkeypatch, row_count
):
    duplicate_edges = _profile("duplicate", 1)
    unseen_shared = _profile("unseen", 2)
    _projected_database(monkeypatch, [{"target": "duplicate"}] * row_count)

    assert await shared_track_profile_ids_for_navigation(
        [duplicate_edges, unseen_shared]
    ) == {"unseen"}
    duplicate_edges.count_nodes.assert_awaited_once()
    unseen_shared.count_nodes.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_row",
    [{}, {"target": None}, {"target": "unexpected"}, {"target": []}, None],
    ids=[
        "missing-target",
        "null-target",
        "foreign-target",
        "invalid-target",
        "invalid-row",
    ],
)
async def test_incomplete_projection_cannot_prove_a_profile_is_dedicated(
    monkeypatch, bad_row
):
    shared = _profile("shared", 2)
    dedicated = _profile("dedicated")
    _projected_database(monkeypatch, [{"target": "shared"}, bad_row])

    assert await shared_track_profile_ids_for_navigation([shared, dedicated]) == {
        "shared"
    }
    shared.count_nodes.assert_awaited_once()
    dedicated.count_nodes.assert_awaited_once()


@pytest.mark.unit
async def test_duplicate_and_dangling_edges_keep_distinct_neighbor_semantics(
    monkeypatch,
):
    duplicate = _profile("duplicate", 1)
    dangling = _profile("dangling", 0)
    _projected_database(
        monkeypatch, [{"target": "duplicate"}] * 2 + [{"target": "dangling"}] * 2
    )

    assert await shared_track_profile_ids_for_navigation([duplicate, dangling]) == set()
    duplicate.count_nodes.assert_awaited_once()
    dangling.count_nodes.assert_awaited_once()


async def _navigation_workspace(client, name):
    response = await client.post("/api/workspaces", json={"name": name})
    assert response.status_code == 200, response.text
    workspace_id = response.json()["workspace"]["id"]
    headers = {"X-Integral-Scope": f"ws:{workspace_id}"}
    tracks, models = [], []
    for index in range(2):
        response = await client.post(
            "/api/tracks", headers=headers, json={"title": f"{name} {index}"}
        )
        assert response.status_code == 200, response.text
        track = await Track.get(response.json()["track"]["id"])
        model = await get_track_attached_operational_model(track)
        assert model is not None
        model.manifest = {
            "operational_model_schema_version": 2,
            "scope": "track",
            "track": {
                "entry_types": [],
                "views": [
                    {
                        "key": "records",
                        "name": "Records",
                        "view_type": "table",
                        "is_default": True,
                    },
                    {"key": "profiles", "name": "Profiles", "view_type": "gallery"},
                ],
                "defaults": {"default_view": "records"},
            },
        }
        await model.save()
        # Seed a distinct saved selection to expose whether sharing is current.
        # The projection must never rewrite the model's template default.
        for existing in await list_scoped_track_views(track.id):
            if existing.is_default:
                existing.is_default = False
                await existing.save()
        await create_track_view(
            track,
            name="Profiles",
            type="gallery",
            is_default=True,
            config={"_manifest_view_key": "profiles"},
        )
        tracks.append(track)
        models.append(model)
    return workspace_id, headers, tracks, models


async def test_http_workspace_switch_and_attachment_change_use_fresh_defaults(
    authenticated_client, test_user, monkeypatch
):
    fixtures = [
        await _navigation_workspace(authenticated_client, name)
        for name in ("Navigation A", "Navigation B")
    ]
    snapshots = {
        model.id: deepcopy(model.manifest)
        for _, _, _, models in fixtures
        for model in models
    }

    async def page(headers, expected):
        response = await authenticated_client.get(
            "/api/tracks", headers=headers, params={"include_entry_counts": "false"}
        )
        assert response.status_code == 200, response.text
        rows = response.json()["tracks"]
        assert {row["id"] for row in rows} == set(expected)
        assert {
            row["id"]: row["operational_model_defaults"]["default_view"] for row in rows
        } == expected

    a_id, a_headers, a_tracks, a_models = fixtures[0]
    _, b_headers, b_tracks, _ = fixtures[1]
    expected_b = {track.id: "records" for track in b_tracks}
    await page(a_headers, {track.id: "records" for track in a_tracks})
    await page(b_headers, expected_b)

    # A second named parent changes the model from dedicated to shared after
    # the initial request. Subsequent pages must not reuse the old classification.
    workspace = await Workspace.get(a_id)
    owner = await get_user_node(test_user.id)
    sibling = await Track.create(
        title="A shared sibling",
        workspace_id=a_id,
        attached_operational_model_id=a_models[0].id,
    )
    await workspace.connect(sibling, edge=CONTAINS)
    await sibling.connect(a_models[0], edge=HAS_OPERATIONAL_MODEL)
    await owner.connect(sibling, edge=OWNS)
    await catalog_track(sibling)
    expected_a = {
        a_tracks[0].id: "profiles",
        a_tracks[1].id: "records",
        sibling.id: "records",
    }
    await page(a_headers, expected_a)
    await page(b_headers, expected_b)
    await page(a_headers, expected_a)

    # A failed batch projection preserves the isolated per-Track fallback.
    singles = [
        await attach_operational_model_defaults_to_track_data({}, track)
        for track in a_tracks
    ]
    monkeypatch.setattr(
        "app.services.operational_model_graph.shared_track_profile_ids_for_navigation",
        AsyncMock(side_effect=RuntimeError("projection unavailable")),
    )
    batched = [{}, {}]
    await attach_operational_model_defaults_to_tracks_data(list(zip(batched, a_tracks)))
    assert batched == singles
    for model_id, manifest in snapshots.items():
        assert (await OperationalModel.get(model_id)).manifest == manifest
