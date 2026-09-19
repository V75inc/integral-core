"""Minimal ANCHORS single-writer E2E via content_profile_graph (Wave 2).

Exercises ``sync_relation_edges`` → ``_sync_anchor_edges`` without the
retired Python seed manifests. Replaces the skipped YAML-loader-dependent
tests in ``test_anchor_integration.py`` / ``test_anchor_seed.py`` for CI
coverage of the sanctioned write path.
"""

from __future__ import annotations

import pytest

from app.models.edges import ANCHORS, CONTAINS, HAS_CONTENT_PROFILE
from app.models.nodes import App, ContentProfile, Entry, Track
from app.services.content_profile_graph import sync_relation_edges


async def _build_space_with_template(
    *,
    workspace_id: str,
    template_key: str = "project-details",
) -> tuple[App, ContentProfile, Track]:
    manifest = {
        "content_profile_schema_version": 2,
        "scope": "app",
        "package": {"slug": "anchor-e2e", "name": "Anchor E2E", "version": "1.0.0"},
        "app": {
            "tracks": [],
            "track_templates": [
                {
                    "key": template_key,
                    "name": "Project Details",
                    "entry_types": [
                        {
                            "key": "note",
                            "name": "Note",
                            "fields": [{"key": "title", "type": "text"}],
                        }
                    ],
                    "views": [],
                    "taxonomy": {"tag_groups": []},
                    "defaults": {},
                }
            ],
            "relations": [],
            "defaults": {},
        },
    }
    app_node = await App.create(
        name="Anchor E2E Space",
        workspace_id=workspace_id,
        owner_user_id="user-anchor-e2e",
    )
    cp = await ContentProfile.create(
        name="Anchor E2E Profile",
        scope="app",
        manifest=manifest,
        app_id=app_node.id,
        workspace_id=workspace_id,
        library_package=False,
    )
    await app_node.connect(cp, edge=HAS_CONTENT_PROFILE)
    app_node.attached_content_profile_id = cp.id
    await app_node.save()

    parent_track = await Track.create(
        title="Projects",
        owner_id="user-anchor-e2e",
        workspace_id=workspace_id,
    )
    await app_node.connect(parent_track, edge=CONTAINS)
    return app_node, cp, parent_track


@pytest.mark.asyncio
async def test_sync_relation_edges_wires_anchors_single_writer():
    """``target='track'`` relation_refs materialize ANCHORS only via graph module."""
    _, _, parent_track = await _build_space_with_template(workspace_id="ws-anchor-e2e")

    detail_track = await Track.create(
        title="Detail Track",
        owner_id="user-anchor-e2e",
        workspace_id="ws-anchor-e2e",
    )
    parent_entry = await Entry.create(
        track_id=parent_track.id,
        title="Parent Project",
        author_id="user-anchor-e2e",
    )
    await parent_track.connect(parent_entry, edge=CONTAINS)

    await sync_relation_edges(
        source_entry=parent_entry,
        relation_refs=[
            {
                "field_key": "details_track",
                "target": "track",
                "targets": [detail_track.id],
            }
        ],
    )

    anchored = await parent_entry.nodes(edge=[ANCHORS], direction="out", node=["Track"])
    assert len(anchored) == 1
    assert anchored[0].id == detail_track.id
