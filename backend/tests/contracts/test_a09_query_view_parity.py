"""A09: exact-query and rendered-projection parity across a page boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agentive.services.query_spec import execute_query_spec
from app.models.edges import CONTAINS
from app.models.nodes import Entry, Track
from app.schemas.query_spec import QueryFilter, QuerySort, QuerySpec

FIXTURE_PATH = (
    Path(__file__).resolve().parents[3]
    / "frontend"
    / "src"
    / "fixtures"
    / "a09QueryProjectionFixture.json"
)


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


async def _create_fixture_track(authenticated_client) -> tuple[str, str, str]:
    """Create the real App/Track graph used by the shared A09 fixture."""
    app_response = await authenticated_client.post(
        "/api/apps",
        json={"name": "A09 Projection Parity", "include_seed_data": False},
    )
    assert app_response.status_code == 200, app_response.text
    app = app_response.json()["app"]

    track_response = await authenticated_client.post(
        "/api/tracks",
        json={
            "title": "A09 Assets",
            "app_id": app["id"],
            "workspace_id": app["workspace_id"],
            "visibility": "private",
        },
    )
    assert track_response.status_code == 200, track_response.text
    track = track_response.json()["track"]
    return app["id"], track["id"], app["workspace_id"]


async def _seed_entries(track_id: str, principal_id: str, fixture: dict) -> None:
    """Seed declared fixture fields without coupling proof to the Post profile.

    Each Entry is attached to its Track immediately, preserving the graph
    contiguousness invariant while keeping this shared query/view fixture
    independent of whichever starter profile an App happens to install.
    """
    track = await Track.get(track_id)
    assert track is not None
    for item in [*fixture["entries"], *fixture["excluded_entries"]]:
        entry = await Entry.create(
            track_id=track_id,
            author_id=principal_id,
            title=item["title"],
            custom_fields=item["custom_fields"],
        )
        await track.connect(entry, edge=CONTAINS)


@pytest.mark.asyncio
async def test_a09_agent_query_and_dashboard_match_shared_page_boundary_fixture(
    authenticated_client, test_user
):
    """Agent query pages and dashboard aggregates return the same 101 records.

    The fixture intentionally has 101 matching records: it crosses the query
    page size of 100 and splits them across consecutive calendar dates. Two
    neighbouring dates are present as exclusions, proving both date boundaries.
    """
    fixture = _fixture()
    app_id, track_id, workspace_id = await _create_fixture_track(authenticated_client)
    principal_id = getattr(test_user, "user_id", None) or test_user.id
    await _seed_entries(track_id, principal_id, fixture)

    spec = QuerySpec(
        resource="entry",
        select=["title", "custom_fields.fixture_key"],
        filters=[
            QueryFilter(
                field="custom_fields.service_due",
                op="gte",
                value=fixture["date_range"]["from"],
            ),
            QueryFilter(
                field="custom_fields.service_due",
                op="lte",
                value=fixture["date_range"]["to"],
            ),
        ],
        sort=[QuerySort(field="custom_fields.fixture_key", direction="asc")],
        limit=100,
        cost_ceiling=1000,
    )
    first_page = await execute_query_spec(
        principal_id=principal_id,
        workspace_id=workspace_id,
        spec=spec,
    )
    assert first_page.items is not None
    assert len(first_page.items) == fixture["page_size"]
    assert first_page.next_cursor
    assert first_page.items[0]["custom_fields.fixture_key"] == "a09-001"
    assert first_page.items[-1]["custom_fields.fixture_key"] == "a09-100"

    second_page = await execute_query_spec(
        principal_id=principal_id,
        workspace_id=workspace_id,
        spec=spec.model_copy(update={"cursor": first_page.next_cursor}),
    )
    assert second_page.items is not None
    assert [row["custom_fields.fixture_key"] for row in second_page.items] == [
        "a09-101"
    ]
    assert second_page.next_cursor is None

    dashboard_response = await authenticated_client.post(
        f"/api/apps/{app_id}/dashboards",
        json={
            "name": "A09 exact coverage",
            "widgets": [
                {
                    "id": "a09-count",
                    "type": "metric_card",
                    "title": "Assets due",
                    "grid": {"x": 0, "y": 0, "w": 4, "h": 2},
                    "data_source": {
                        "kind": "count",
                        "track_id": track_id,
                        "filters": [
                            {
                                "field": "custom_fields.service_due",
                                "op": "gte",
                                "value": fixture["date_range"]["from"],
                            },
                            {
                                "field": "custom_fields.service_due",
                                "op": "lte",
                                "value": fixture["date_range"]["to"],
                            },
                        ],
                    },
                },
                {
                    "id": "a09-dates",
                    "type": "chart_bar",
                    "title": "Due by date",
                    "grid": {"x": 4, "y": 0, "w": 8, "h": 2},
                    "data_source": {
                        "track_id": track_id,
                        "group_by": "custom_fields.service_due",
                        "filters": [
                            {
                                "field": "custom_fields.service_due",
                                "op": "gte",
                                "value": fixture["date_range"]["from"],
                            },
                            {
                                "field": "custom_fields.service_due",
                                "op": "lte",
                                "value": fixture["date_range"]["to"],
                            },
                        ],
                    },
                },
            ],
        },
    )
    assert dashboard_response.status_code == 200, dashboard_response.text
    dashboard_id = dashboard_response.json()["id"]
    data_response = await authenticated_client.get(
        f"/api/apps/{app_id}/dashboards/{dashboard_id}/data",
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
    )
    assert data_response.status_code == 200, data_response.text
    widget_data = data_response.json()["widget_data"]
    assert widget_data["a09-count"] == {"value": 101, "total_matched": 101}
    assert widget_data["a09-dates"]["series"] == [
        {"label": "2026-09-30", "value": 51},
        {"label": "2026-10-01", "value": 50},
    ]
