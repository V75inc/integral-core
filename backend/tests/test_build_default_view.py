"""A built Track opens on its most useful specific view, not the Feed."""

from __future__ import annotations

import pytest

from app.services.design_blueprint import validate_blueprint
from tests.test_build_anchors import _PROPOSAL, _approved_thread, _blueprint, _plan

pytestmark = pytest.mark.smoke

_VIEWS = [
    {
        "id": "view.table",
        "track": "track.projects",
        "name": "All projects",
        "type": "table",
        "decision": "See every project",
    },
    {
        "id": "view.board",
        "track": "track.projects",
        "name": "Cards",
        "type": "gallery",
        "decision": "Browse projects at a glance",
    },
]


def _plan_views() -> list:
    return [
        {
            "tool": "integral_save_view",
            "args": {
                "track_id": "{{track.id:Projects}}",
                "name": v["name"],
                "view_type": v["type"],
                "config": {},
            },
        }
        for v in _VIEWS
    ]


def test_blueprint_allows_one_default_view_per_track() -> None:
    blueprint = _blueprint()
    blueprint["views"] = [{**v, "is_default": True} for v in _VIEWS]
    _, error = validate_blueprint(blueprint)
    assert "only one view per track is_default" in error


async def _built_views(authenticated_client, test_user, name: str, views: list):
    from app.agentive import staging
    from app.models.nodes import Track
    from app.services.app_graph import get_track_attached_operational_model
    from app.services.operational_model_graph import sync_attached_manifest

    blueprint = _blueprint()
    blueprint["views"] = views
    ws, call, approve = await _approved_thread(authenticated_client, test_user, name)
    proposed = await call(
        "integral_propose_design",
        summary="Studio",
        proposal=_PROPOSAL
        + " Views: All projects (table), Cards (gallery)."
        + " Seeds: Website (Acme), Brand refresh (Globex).",
        blueprint=blueprint,
    )
    assert not proposed.is_error, proposed
    await approve()

    built = await call(
        "integral_build_approved_design", operations=_plan() + _plan_views()
    )
    assert not built.is_error, built
    assert built.data["applied"] is True, built.data
    staging._reset_for_tests()

    track = next(
        t
        for t in await Track.find({"context.title": "Projects"})
        if t.workspace_id == ws
    )
    model = await get_track_attached_operational_model(track)
    await sync_attached_manifest(model)
    response = await authenticated_client.get(
        f"/api/tracks/{track.id}/views", headers={"X-Integral-Scope": f"ws:{ws}"}
    )
    assert response.status_code == 200, response.text
    return {v["name"]: v["is_default"] for v in response.json()["views"]}


@pytest.mark.asyncio
async def test_design_marked_view_opens_the_track(
    authenticated_client, test_user
) -> None:
    views = [dict(_VIEWS[0]), {**_VIEWS[1], "is_default": True}]
    defaults = await _built_views(
        authenticated_client, test_user, "Default marked", views
    )
    assert [name for name, on in defaults.items() if on] == ["Cards"]


@pytest.mark.asyncio
async def test_first_specific_view_opens_the_track_when_unmarked(
    authenticated_client, test_user
) -> None:
    defaults = await _built_views(
        authenticated_client, test_user, "Default unmarked", [dict(v) for v in _VIEWS]
    )
    assert [name for name, on in defaults.items() if on] == ["All projects"]
    assert defaults.get("Feed") is False
