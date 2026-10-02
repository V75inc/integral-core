"""C6 A05 field identity exercised through the shipped consumers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agentive.services.query_spec import execute_query_spec
from app.models.edges import CONTAINS
from app.models.nodes import Entry, Track
from app.schemas.query_spec import QueryFilter, QuerySpec

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "frontend"
    / "src"
    / "fixtures"
    / "c6A05FieldProjection.json"
)


@pytest.mark.asyncio
async def test_a05_agent_query_resolves_collision_null_and_renamed_key(
    authenticated_client, test_user
):
    """Agent query projects the same real persisted fixture used in the UI."""
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    app_response = await authenticated_client.post(
        "/api/apps", json={"name": "C6 A05 fields", "include_seed_data": False}
    )
    assert app_response.status_code == 200, app_response.text
    app = app_response.json()["app"]
    track_response = await authenticated_client.post(
        "/api/tracks",
        json={
            "title": "Assets",
            "app_id": app["id"],
            "workspace_id": app["workspace_id"],
            "visibility": "private",
        },
    )
    assert track_response.status_code == 200, track_response.text
    track_id = track_response.json()["track"]["id"]
    track = await Track.get(track_id)
    assert track is not None
    source = fixture["entry"]
    record = await Entry.create(
        track_id=track_id,
        author_id=getattr(test_user, "user_id", None) or test_user.id,
        title=source["title"],
        status=source["status"],
        custom_fields=source["custom_fields"],
    )
    await track.connect(record, edge=CONTAINS)

    result = await execute_query_spec(
        principal_id=getattr(test_user, "user_id", None) or test_user.id,
        workspace_id=app["workspace_id"],
        spec=QuerySpec(
            resource="entry",
            select=[
                "id",
                "status",
                "custom_fields.status",
                "custom_fields.workflow_state",
                "custom_fields.registration_number",
            ],
            filters=[
                QueryFilter(field="custom_fields.fixture_key", op="eq", value="c6-a05")
            ],
            limit=10,
            cost_ceiling=100,
        ),
    )

    assert result.items == [
        {
            "id": record.id,
            "status": "active",
            "custom_fields.status": "awaiting_parts",
            "custom_fields.workflow_state": None,
            "custom_fields.registration_number": "ABC-014",
        }
    ]
