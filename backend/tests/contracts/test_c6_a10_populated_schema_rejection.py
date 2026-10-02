"""C6 A10 verifies reject-before-swap against populated Postgres graph data."""

from __future__ import annotations

import pytest

from app.exceptions import BadRequestError
from app.models.edges import CONTAINS
from app.models.nodes import Entry, EntryType, OperationalModel, Track
from app.services.operational_model_atomic_swap import fork_draft, publish_draft


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_a10_populated_breaking_schema_rejected_without_mutation(
    authenticated_client, test_user
):
    app_response = await authenticated_client.post(
        "/api/apps",
        json={"name": "C6 A10 populated schema", "include_seed_data": False},
    )
    assert app_response.status_code == 200, app_response.text
    app = app_response.json()["app"]
    track_response = await authenticated_client.post(
        "/api/tracks",
        json={
            "title": "Populated assets",
            "app_id": app["id"],
            "workspace_id": app["workspace_id"],
            "visibility": "private",
        },
    )
    assert track_response.status_code == 200, track_response.text
    track_id = track_response.json()["track"]["id"]
    track = await Track.get(track_id)
    assert track is not None
    published = await OperationalModel.get(track.attached_operational_model_id)
    assert published is not None
    original_manifest = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "package": {"name": "C6 A10 assets", "version": "1.0.0"},
        "track": {
            "entry_types": [
                {
                    "key": "asset",
                    "name": "Asset",
                    "fields": [
                        {
                            "key": "registration_number",
                            "name": "Registration",
                            "type": "text",
                        },
                        {"key": "workflow_state", "name": "Workflow", "type": "text"},
                    ],
                }
            ],
            "views": [],
            "taxonomy": {"tag_groups": []},
        },
    }
    published.manifest = original_manifest
    published.scope = "track"
    await published.save()
    entry_type = await EntryType.create(
        name="Asset",
        track_id=track.id,
        form_schema={
            "_manifest_entry_type_key": "asset",
            "fields": [
                {"key": "registration_number", "name": "Registration", "type": "text"},
                {"key": "workflow_state", "name": "Workflow", "type": "text"},
            ],
        },
    )
    await published.connect(entry_type, edge=CONTAINS)
    entry = await Entry.create(
        type_id=entry_type.id,
        track_id=track.id,
        author_id=getattr(test_user, "user_id", None) or test_user.id,
        title="Van 14",
        custom_fields={
            "registration_number": "ABC-014",
            "workflow_state": "awaiting_parts",
        },
        schema_revision=published.version_number,
    )
    await track.connect(entry, edge=CONTAINS)

    draft = await fork_draft(published=published, actor_id=test_user.id)
    draft.manifest = {
        **original_manifest,
        "track": {
            **original_manifest["track"],
            "entry_types": [
                {
                    **original_manifest["track"]["entry_types"][0],
                    "fields": [
                        original_manifest["track"]["entry_types"][0]["fields"][0]
                    ],
                }
            ],
        },
    }
    await draft.save()

    with pytest.raises(BadRequestError, match="no migration path") as rejected:
        await publish_draft(draft=draft, published=published, run_migrations=True)

    assert rejected.value.details["unhandled_breaks"]
    refreshed_published = await OperationalModel.get(published.id)
    refreshed_draft = await OperationalModel.get(draft.id)
    refreshed_entry = await Entry.get(entry.id)
    assert refreshed_published.manifest == original_manifest
    assert refreshed_published.version_number == 1
    assert refreshed_draft.status == "draft"
    assert refreshed_entry.custom_fields == {
        "registration_number": "ABC-014",
        "workflow_state": "awaiting_parts",
    }
    assert refreshed_entry.schema_revision == 1
