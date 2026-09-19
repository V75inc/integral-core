"""materialize_anchor_track + auto-provision hook tests — Phase 3.1 Plan 03.1-02 Task 3 (ANC-04 + ANC-08).

Covers:
  - materialize_anchor_track happy path: anchored Track lives in same App +
    same Workspace, carries HAS_CONTENT_PROFILE + TEMPLATED_FROM edges by
    reference, and Track.attached_content_profile_id scalar is in lockstep.
  - By-reference contract: a SECOND anchored Track from the same template_key
    in the same App reuses the SAME ContentProfile node.
  - validate_and_materialize_entry_custom_fields auto-provision hook: a
    relation field with target='track' + auto_provision=True + no incoming
    value lazily provisions an anchor Track and seeds relation_refs so
    sync_relation_edges wires ANCHORS edges.
  - _maybe_reuse_existing_anchor reuses an existing per-(entry, field_key)
    anchor (update flow idempotency).
  - Same-workspace gate (ANC-08): materialize_anchor_track always emits a
    Track in source_track.workspace_id (defensive belt+suspenders).
  - Failure paths: no parent App, no space-attached CP, unknown
    template_key all raise BadRequestError.

Mirrors test patterns from prior plans. No ``app.main`` import (deferred-items.md).
"""

from __future__ import annotations

import pytest

from app.exceptions import BadRequestError
from app.models.edges import ANCHORS, CONTAINS, HAS_CONTENT_PROFILE, TEMPLATED_FROM
from app.models.nodes import App, ContentProfile, Entry, EntryType, Track
from app.services.app_graph import get_track_attached_content_profile
from app.services.content_profile_compile import _normalize_field_spec
from app.services.content_profile_entry_fields import (
    validate_and_materialize_entry_custom_fields,
)
from app.services.content_profile_graph import (
    _maybe_reuse_existing_anchor,
    materialize_anchor_track,
    sync_relation_edges,
)


def _make_space_cp_manifest_with_template(template_key: str, template_name: str):
    """Build an App-scope ContentProfile manifest declaring one track template."""
    return {
        "content_profile_schema_version": 2,
        "scope": "app",
        "package": {"slug": "t", "name": "T", "version": "1.0.0"},
        "app": {
            "tracks": [],
            "track_templates": [
                {
                    "key": template_key,
                    "name": template_name,
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


async def _build_space_with_template(
    *,
    workspace_id: str,
    space_name: str,
    template_key: str,
    template_name: str = "Detail Track",
):
    """Create a App + space-attached ContentProfile declaring one track template."""
    app_node = await App.create(
        name=space_name,
        workspace_id=workspace_id,
        owner_user_id="user-anc-1",
    )
    cp = await ContentProfile.create(
        name=f"{space_name} Profile",
        scope="app",
        manifest=_make_space_cp_manifest_with_template(template_key, template_name),
        app_id=app_node.id,
        workspace_id=workspace_id,
        library_package=False,
    )
    await app_node.connect(cp, edge=HAS_CONTENT_PROFILE)
    app_node.attached_content_profile_id = cp.id
    await app_node.save()
    return app_node, cp


async def _track_inside(app_node: App, *, title: str, workspace_id: str) -> Track:
    """Create a Track contained by the given App."""
    track = await Track.create(
        title=title, owner_id="user-anc-1", workspace_id=workspace_id
    )
    await app_node.connect(track, edge=CONTAINS)
    return track


@pytest.mark.asyncio
async def test_materialize_anchor_track_happy_path():
    """Anchor Track lands in same workspace, same App, with both edges + scalar."""
    ws = "ws-mat-1"
    app_node, _scp = await _build_space_with_template(
        workspace_id=ws,
        space_name="Workspace App",
        template_key="project-details",
        template_name="Project Details",
    )
    source_track = await _track_inside(app_node, title="Projects", workspace_id=ws)

    anchor = await materialize_anchor_track(
        source_track=source_track,
        template_key="project-details",
        field_key="details",
    )

    # Workspace + lineage scalar invariants.
    assert anchor.workspace_id == ws
    assert anchor.template_id == "project-details"
    assert anchor.attached_content_profile_id  # scalar set

    # Lives in the same App (CONTAINS edge).
    parents = await anchor.nodes(
        edge=["CONTAINS"], direction="in", node=["WorkspaceApp"]
    )
    parent_ids = {p.id for p in parents}
    assert app_node.id in parent_ids

    # HAS_CONTENT_PROFILE edge points at a ContentProfile (the template CP).
    cps = await anchor.nodes(
        edge=["HAS_CONTENT_PROFILE"], direction="out", node=["ContentProfile"]
    )
    assert len(cps) == 1
    template_cp_id = cps[0].id
    assert anchor.attached_content_profile_id == template_cp_id

    # TEMPLATED_FROM edge points at the SAME ContentProfile and carries the
    # template_key payload.
    lineage = await anchor.nodes(
        edge=[TEMPLATED_FROM], direction="out", node=["ContentProfile"]
    )
    assert len(lineage) == 1
    assert lineage[0].id == template_cp_id


@pytest.mark.asyncio
async def test_materialize_anchor_track_materializes_template_views():
    """Template CP gains View nodes at anchor-create so the tab strip is non-empty."""
    from app.api.views import _list_track_views

    ws = "ws-mat-viewtabs"
    app_node = await App.create(
        name="Projects App",
        workspace_id=ws,
        owner_user_id="user-anc-views",
    )
    manifest = _make_space_cp_manifest_with_template("project-details", "Project")
    manifest["app"]["track_templates"][0]["views"] = [
        {
            "key": "tasks-board",
            "name": "Tasks",
            "view_type": "kanban",
            "entry_types": ["task"],
            "default_entry_type": "task",
            "group_by": "custom_fields.bucket",
            "kanban_columns": [
                {"key": "backlog", "label": "Backlog"},
                {"key": "done", "label": "Done"},
            ],
        }
    ]
    manifest["app"]["track_templates"][0]["defaults"] = {"default_view": "tasks-board"}
    cp = await ContentProfile.create(
        name="Projects Profile",
        scope="app",
        manifest=manifest,
        app_id=app_node.id,
        workspace_id=ws,
        library_package=False,
    )
    await app_node.connect(cp, edge=HAS_CONTENT_PROFILE)
    app_node.attached_content_profile_id = cp.id
    await app_node.save()

    source_track = await _track_inside(app_node, title="Projects", workspace_id=ws)
    anchor = await materialize_anchor_track(
        source_track=source_track,
        template_key="project-details",
        field_key="details_track",
        source_entry_title="Litware data exchange",
    )

    views = await _list_track_views(anchor)
    assert len(views) >= 1
    tasks_board = next(
        v
        for v in views
        if str((v.config or {}).get("_manifest_view_key") or "") == "tasks-board"
    )
    assert tasks_board.type == "kanban"
    assert (tasks_board.config or {}).get("group_by") == "custom_fields.bucket"
    assert str(getattr(tasks_board, "track_id", "") or "") == anchor.id
    assert bool(getattr(tasks_board, "is_default", False)) is True


@pytest.mark.asyncio
async def test_materialize_anchor_track_by_reference_reuses_template_cp():
    """Two anchored Tracks from the same template_key share ONE ContentProfile by reference."""
    ws = "ws-mat-byref"
    app_node, _scp = await _build_space_with_template(
        workspace_id=ws,
        space_name="Byref App",
        template_key="detail",
        template_name="Detail",
    )
    src = await _track_inside(app_node, title="Source", workspace_id=ws)

    a1 = await materialize_anchor_track(
        source_track=src, template_key="detail", field_key="d1"
    )
    a2 = await materialize_anchor_track(
        source_track=src, template_key="detail", field_key="d2"
    )

    assert a1.id != a2.id  # distinct Tracks
    assert a1.attached_content_profile_id  # both set
    assert a2.attached_content_profile_id
    # By-reference: same CP id.
    assert a1.attached_content_profile_id == a2.attached_content_profile_id

    # And both Tracks point at it via HAS_CONTENT_PROFILE.
    cp_a1 = (await a1.nodes(edge=["HAS_CONTENT_PROFILE"], direction="out"))[0].id
    cp_a2 = (await a2.nodes(edge=["HAS_CONTENT_PROFILE"], direction="out"))[0].id
    assert cp_a1 == cp_a2


@pytest.mark.asyncio
async def test_materialize_anchor_track_reheals_template_cp_missing_view_nodes():
    """Found live (2026-08-15): a template CP's ``manifest`` can already
    match the current spec text while its real View graph nodes were never
    created — e.g. ``_materialize_template_content_profile_nodes`` raised
    the first time (a view_type wasn't registered yet in that process) and
    the exception was swallowed upstream. The by-reference cache-hit path
    in ``_resolve_or_create_template_content_profile`` only re-materializes
    on a manifest TEXT diff, so a CP stuck this way stayed broken forever —
    every anchored Track sharing it showed a permanent "View not found".
    6 of 10 real space_track_template CPs in the live DB were found in this
    exact state. This test simulates it directly (delete the real View
    nodes without touching the manifest) and asserts a second
    materialize_anchor_track call for the same template_key heals it.
    """
    ws = "ws-mat-reheal"
    app_node, _scp = await _build_space_with_template(
        workspace_id=ws,
        space_name="Reheal App",
        template_key="detail",
        template_name="Detail",
    )
    # Override the bare "views": [] template with one real view spec.
    manifest = _make_space_cp_manifest_with_template("detail", "Detail")
    manifest["app"]["track_templates"][0]["views"] = [
        {"key": "v1", "name": "V1", "view_type": "feed"}
    ]
    _scp.manifest = manifest
    await _scp.save()

    src = await _track_inside(app_node, title="Source", workspace_id=ws)
    a1 = await materialize_anchor_track(
        source_track=src, template_key="detail", field_key="d1"
    )
    tcp = await get_track_attached_content_profile(a1)
    assert tcp is not None

    from app.models.edges import CATALOGS
    from app.services.app_graph import get_or_create_views_registry_for_content_profile

    vreg = await get_or_create_views_registry_for_content_profile(tcp)
    views = await vreg.nodes(edge=[CATALOGS], node=["View"])
    assert len(views) >= 1  # materialized correctly the first time

    # Simulate the swallowed-exception failure mode: real View nodes gone,
    # manifest left untouched (still matches the current spec exactly).
    for v in views:
        await v.delete()
    views = await vreg.nodes(edge=[CATALOGS], node=["View"])
    assert views == []

    # A second anchor from the SAME template_key hits the by-reference
    # cache-hit path — manifest text is unchanged, so the OLD guard would
    # skip re-materialization entirely and this would stay broken forever.
    a2 = await materialize_anchor_track(
        source_track=src, template_key="detail", field_key="d2"
    )
    assert a2.attached_content_profile_id == a1.attached_content_profile_id

    healed_views = await vreg.nodes(edge=[CATALOGS], node=["View"])
    assert len(healed_views) >= 1
    assert any((v.config or {}).get("_manifest_view_key") == "v1" for v in healed_views)


@pytest.mark.asyncio
async def test_materialize_anchor_track_rejects_no_parent_space():
    """A Track with no parent App cannot anchor (templates live on Apps)."""
    standalone = await Track.create(
        title="Standalone", owner_id="user-x", workspace_id="ws-no-space"
    )
    with pytest.raises(BadRequestError) as ei:
        await materialize_anchor_track(
            source_track=standalone, template_key="x", field_key="f"
        )
    assert "parent App" in str(ei.value)


@pytest.mark.asyncio
async def test_materialize_anchor_track_rejects_no_space_cp():
    """A App without an attached CP cannot resolve track templates."""
    ws = "ws-no-cp"
    app_node = await App.create(name="No CP", workspace_id=ws, owner_user_id="user-y")
    src = await _track_inside(app_node, title="Source", workspace_id=ws)
    with pytest.raises(BadRequestError) as ei:
        await materialize_anchor_track(
            source_track=src, template_key="any", field_key="f"
        )
    assert "App-attached ContentProfile" in str(ei.value)


@pytest.mark.asyncio
async def test_materialize_anchor_track_rejects_unknown_template_key():
    """Unknown template_key raises BadRequestError."""
    ws = "ws-no-tmpl"
    app_node, _scp = await _build_space_with_template(
        workspace_id=ws,
        space_name="Known Templates Only",
        template_key="known",
        template_name="Known",
    )
    src = await _track_inside(app_node, title="Source", workspace_id=ws)
    with pytest.raises(BadRequestError) as ei:
        await materialize_anchor_track(
            source_track=src, template_key="unknown", field_key="f"
        )
    assert "not found" in str(ei.value)
    assert "unknown" in str(ei.value)


@pytest.mark.asyncio
async def test_materialize_anchor_track_title_falls_back_to_template_name():
    """Without source_entry_title the anchor Track title is just the template name."""
    ws = "ws-title-fallback"
    app_node, _scp = await _build_space_with_template(
        workspace_id=ws,
        space_name="Fallback App",
        template_key="project-details",
        template_name="Project Details",
    )
    src = await _track_inside(app_node, title="Projects", workspace_id=ws)

    anchor = await materialize_anchor_track(
        source_track=src, template_key="project-details", field_key="d"
    )

    assert anchor.title == "Project Details"


@pytest.mark.asyncio
async def test_materialize_anchor_track_title_suffixes_source_entry_title():
    """source_entry_title makes sibling anchor Tracks visually distinct."""
    ws = "ws-title-suffix"
    app_node, _scp = await _build_space_with_template(
        workspace_id=ws,
        space_name="Suffix App",
        template_key="project-details",
        template_name="Project Details",
    )
    src = await _track_inside(app_node, title="Projects", workspace_id=ws)

    a1 = await materialize_anchor_track(
        source_track=src,
        template_key="project-details",
        field_key="d",
        source_entry_title="Onboarding: Contoso",
    )
    a2 = await materialize_anchor_track(
        source_track=src,
        template_key="project-details",
        field_key="d",
        source_entry_title="API hardening sprint",
    )

    assert a1.title == "Project Details: Onboarding: Contoso"
    assert a2.title == "Project Details: API hardening sprint"
    assert a1.id != a2.id


@pytest.mark.asyncio
async def test_materialize_anchor_track_anc08_same_workspace():
    """ANC-08: anchored Track always in source_track.workspace_id."""
    ws = "ws-anc-08"
    app_node, _scp = await _build_space_with_template(
        workspace_id=ws,
        space_name="ANC08",
        template_key="t",
        template_name="T",
    )
    src = await _track_inside(app_node, title="Source ANC08", workspace_id=ws)
    anchor = await materialize_anchor_track(
        source_track=src, template_key="t", field_key="f"
    )
    assert anchor.workspace_id == ws


@pytest.mark.asyncio
async def test_auto_provision_hook_fires_when_value_absent():
    """validate_and_materialize_entry_custom_fields auto-provisions on absent value."""
    ws = "ws-auto-1"
    app_node, _scp = await _build_space_with_template(
        workspace_id=ws,
        space_name="Auto Provision",
        template_key="details",
        template_name="Details",
    )
    src = await _track_inside(app_node, title="Source AP", workspace_id=ws)

    # EntryType with a single relation field (target='track', auto_provision=True).
    et = await EntryType.create(
        name="Card", icon="document", track_id=src.id, is_template=False
    )

    runtime_tier = {
        "entry_types": [
            {
                "key": "card",
                "name": "Card",
                "fields": [
                    _normalize_field_spec(
                        {
                            "key": "details",
                            "type": "relation",
                            "relation": {
                                "target": "track",
                                "target_track_template": "details",
                                "auto_provision": True,
                            },
                        }
                    )
                ],
                "base_fields": {},
                "required_tag_groups": [],
            }
        ],
        "views": [],
        "taxonomy": {"tag_groups": []},
        "defaults": {},
    }

    out, relation_refs = await validate_and_materialize_entry_custom_fields(
        track=src,
        entry_type=et,
        custom_fields={},  # value absent
        runtime_tier=runtime_tier,
    )

    # out["details"] holds the anchored Track id (many=False → scalar).
    assert isinstance(out.get("details"), str)
    anchored_id = out["details"]
    anchor_track = await Track.get(anchored_id)
    assert anchor_track is not None
    assert anchor_track.workspace_id == ws
    assert anchor_track.template_id == "details"

    # relation_refs carries a track-target ref for sync_relation_edges.
    assert len(relation_refs) == 1
    ref = relation_refs[0]
    assert ref["field_key"] == "details"
    assert ref["target"] == "track"
    assert ref["targets"] == [anchored_id]
    assert ref["auto_provision"] is True


@pytest.mark.asyncio
async def test_auto_provision_hook_then_sync_relation_edges_wires_anchors():
    """End-to-end: auto-provision → relation_refs → sync_relation_edges wires ANCHORS."""
    ws = "ws-auto-2"
    app_node, _scp = await _build_space_with_template(
        workspace_id=ws,
        space_name="E2E",
        template_key="details",
        template_name="Details",
    )
    src = await _track_inside(app_node, title="Source E2E", workspace_id=ws)
    et = await EntryType.create(
        name="Card", icon="document", track_id=src.id, is_template=False
    )
    runtime_tier = {
        "entry_types": [
            {
                "key": "card",
                "name": "Card",
                "fields": [
                    _normalize_field_spec(
                        {
                            "key": "details",
                            "type": "relation",
                            "relation": {
                                "target": "track",
                                "target_track_template": "details",
                                "auto_provision": True,
                            },
                        }
                    )
                ],
                "base_fields": {},
                "required_tag_groups": [],
            }
        ],
        "views": [],
        "taxonomy": {"tag_groups": []},
        "defaults": {},
    }
    _out, relation_refs = await validate_and_materialize_entry_custom_fields(
        track=src,
        entry_type=et,
        custom_fields={},
        runtime_tier=runtime_tier,
    )

    # Create the source Entry on the source track and run sync_relation_edges
    # with the auto-provision relation_refs. ANCHORS edge must be wired.
    source_entry = await Entry.create(
        title="Card E2E",
        track_id=src.id,
        author_id="user-anc-1",
        type_id=et.id,
    )
    await sync_relation_edges(source_entry=source_entry, relation_refs=relation_refs)

    anchored = await source_entry.nodes(edge=[ANCHORS], direction="out", node=["Track"])
    anchored_ids = {a.id for a in anchored}
    assert anchored_ids == {relation_refs[0]["targets"][0]}


@pytest.mark.asyncio
async def test_maybe_reuse_existing_anchor_reuses_on_update_flow():
    """If entry already anchors a Track for field_key, the hook reuses it
    instead of provisioning a new one (idempotent update path)."""
    ws = "ws-reuse"
    app_node, _scp = await _build_space_with_template(
        workspace_id=ws,
        space_name="Reuse",
        template_key="d",
        template_name="D",
    )
    src = await _track_inside(app_node, title="Source Reuse", workspace_id=ws)
    et = await EntryType.create(
        name="Card", icon="document", track_id=src.id, is_template=False
    )

    # First create flow: provision an anchor + create source entry + wire ANCHORS.
    rf = (
        await _normalize_field_spec(
            {
                "key": "details",
                "type": "relation",
                "relation": {
                    "target": "track",
                    "target_track_template": "d",
                    "auto_provision": True,
                },
            }
        )
        if False
        else _normalize_field_spec(
            {
                "key": "details",
                "type": "relation",
                "relation": {
                    "target": "track",
                    "target_track_template": "d",
                    "auto_provision": True,
                },
            }
        )
    )
    runtime_tier = {
        "entry_types": [
            {
                "key": "card",
                "name": "Card",
                "fields": [rf],
                "base_fields": {},
                "required_tag_groups": [],
            }
        ],
        "views": [],
        "taxonomy": {"tag_groups": []},
        "defaults": {},
    }
    _out_a, refs_a = await validate_and_materialize_entry_custom_fields(
        track=src, entry_type=et, custom_fields={}, runtime_tier=runtime_tier
    )
    source_entry = await Entry.create(
        title="Card Reuse",
        track_id=src.id,
        author_id="user-anc-1",
        type_id=et.id,
    )
    await sync_relation_edges(source_entry=source_entry, relation_refs=refs_a)
    first_anchor_id = refs_a[0]["targets"][0]

    # _maybe_reuse_existing_anchor finds it.
    found = await _maybe_reuse_existing_anchor(
        source_entry=source_entry, field_key="details"
    )
    assert found == first_anchor_id

    # Update flow: call validate_and_materialize_entry_custom_fields again
    # WITH the existing entry — same anchor id must come back.
    _out_b, refs_b = await validate_and_materialize_entry_custom_fields(
        track=src,
        entry_type=et,
        custom_fields={},
        runtime_tier=runtime_tier,
        entry=source_entry,
    )
    assert refs_b[0]["targets"] == [first_anchor_id]


@pytest.mark.asyncio
async def test_auto_provision_template_cp_attached_to_anchored_track():
    """Auto-provisioned Track gets the template CP via HAS_CONTENT_PROFILE
    AND attached_content_profile_id scalar in lockstep — verifies the
    by-reference contract end-to-end through the hook."""
    ws = "ws-cp-lockstep"
    app_node, _scp = await _build_space_with_template(
        workspace_id=ws,
        space_name="Lockstep",
        template_key="d",
        template_name="D",
    )
    src = await _track_inside(app_node, title="Source CP", workspace_id=ws)
    anchor = await materialize_anchor_track(
        source_track=src, template_key="d", field_key="f"
    )

    tcp = await get_track_attached_content_profile(anchor)
    assert tcp is not None
    assert anchor.attached_content_profile_id == tcp.id

    # Manifest carries the materialization discriminator.
    mat = (tcp.manifest or {}).get("materialization") or {}
    assert mat.get("kind") == "space_track_template"
    assert mat.get("template_key") == "d"
    assert mat.get("app_id") == app_node.id


@pytest.mark.asyncio
async def test_anchored_track_entry_types_resolve_via_shared_content_profile():
    """Anchored tracks expose track-scoped entry types through their shared CP."""
    ws = "ws-anchored-entry-types"
    app_node, _scp = await _build_space_with_template(
        workspace_id=ws,
        space_name="Anchored Entry Types",
        template_key="d",
        template_name="D",
    )
    src = await _track_inside(app_node, title="Source", workspace_id=ws)
    anchor = await materialize_anchor_track(
        source_track=src, template_key="d", field_key="f"
    )

    # Materialization now writes a track-scoped copy for direct endpoint lookup.
    scalar_matches = await EntryType.find({"context.track_id": anchor.id})
    assert len(scalar_matches) == 1

    # The fallback the fix added: resolve via the attached ContentProfile.
    tcp = await get_track_attached_content_profile(anchor)
    assert tcp is not None
    fallback_entry_types = await tcp.nodes(edge=[CONTAINS], node=["EntryType"])
    assert len(fallback_entry_types) == 2
    assert scalar_matches[0].id in {
        entry_type.id for entry_type in fallback_entry_types
    }
    assert fallback_entry_types[0].name == "Note"
    assert fallback_entry_types[0].form_schema.get("fields")[0]["key"] == "title"
