"""Install-time smoke test for the Region Gallery demo app — proves the
manifest actually compiles and installs (every region view type +
layout_container mode + all four create_wizard step kinds resolve), and
that its wizard tools work end to end against a real DB.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.edges import CONTAINS, IS_MEMBER_OF
from app.models.nodes import App, ContentProfile, Track, User, Workspace
from app.services.app_lifecycle import install_app
from app.services.content_profile_loader import load_library_profiles_with_issues
from app.services.content_profile_plugins import (
    discover_and_register_plugins,
    reset_discovered_for_tests,
)
from app.services.hooks.registry import ToolContext
from app.utils.time import utc_now_iso


@pytest.fixture(scope="module", autouse=True)
def _view_types():
    reset_discovered_for_tests()
    discover_and_register_plugins()
    yield
    reset_discovered_for_tests()


async def _make_workspace() -> tuple[Workspace, str]:
    now = utc_now_iso()
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Region Gallery Test",
        name_fold="region gallery test",
        created_at=now,
        updated_at=now,
    )
    owner = await User.create(name="Region Gallery Test owner")
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner", joined_at=now)
    return ws, owner.id


async def _load_library_cp(slug: str) -> ContentProfile:
    specs, issues = load_library_profiles_with_issues(
        profiles_root=Path("app/profiles")
    )
    matching = [s for s in specs if s.slug == slug]
    assert matching, f"{slug} failed to load: {[i for i in issues if slug in str(i)]}"
    spec = matching[0]
    manifest = dict(spec.manifest)
    pkg = dict(manifest.get("package") or {})
    pkg["version"] = spec.version or pkg.get("version") or "1.0.0"
    manifest["package"] = pkg
    now = utc_now_iso()
    return await ContentProfile.create(
        name=manifest["package"]["name"],
        scope="app",
        manifest=manifest,
        library_package=True,
        version=manifest["package"]["version"],
        created_at=now,
        updated_at=now,
    )


async def _track_by_title(app_node: App, title: str) -> Track:
    tracks = await app_node.nodes(edge=[CONTAINS], node=["Track"])
    return next(t for t in tracks if getattr(t, "title", "") == title)


@pytest.mark.asyncio
async def test_install_materializes_every_region_view_and_wizard():
    ws, owner_id = await _make_workspace()
    lib = await _load_library_cp("region-gallery")
    result = await install_app(
        workspace_id=ws.id, library_cp_id=lib.id, actor_id=owner_id
    )
    app_node = await App.get(result["app_id"])
    assert app_node is not None

    from app.models.nodes import EntryType

    track = await _track_by_title(app_node, "Showcase Records")
    ets = await EntryType.find(
        {"context.track_id": track.id, "context.name": "Showcase Record"}
    )
    assert len(ets) == 1
    fs = ets[0].form_schema or {}
    assert "create_wizard" in fs and fs["create_wizard"] is not None
    kinds = [s.get("kind") for s in fs["create_wizard"]["steps"]]
    assert kinds == ["form", "period_picker", "entry_checklist", "summary"]


@pytest.mark.asyncio
async def test_wizard_tools_work_end_to_end():
    ws, owner_id = await _make_workspace()
    lib = await _load_library_cp("region-gallery")
    result = await install_app(
        workspace_id=ws.id, library_cp_id=lib.id, actor_id=owner_id
    )
    app_node = await App.get(result["app_id"])
    ctx = ToolContext(user_id=owner_id, workspace_id=ws.id, scope="test")

    track = await _track_by_title(app_node, "Showcase Records")

    from app.profiles.region_gallery.tools.demo_tools import (  # type: ignore[import]
        demo_compute_upcoming_cycles,
        demo_link_selected_records,
        demo_prefill_form,
    )

    prefill = await demo_prefill_form({}, ctx)
    assert prefill["status"] == "Draft"

    cycles = await demo_compute_upcoming_cycles({"cadence": "monthly", "count": 3}, ctx)
    assert cycles["ok"] is True
    assert len(cycles["periods"]) == 3
    assert all("cycle_start" in p for p in cycles["periods"])

    # Create two real records, then run on_create_tool against a third as
    # if the wizard had checked the first two in its entry_checklist step.
    record_a = await ctx.create_entry(
        track_id=track.id,
        entry_type_key="showcase_record",
        title="Record A",
        custom_fields={"status": "Active", "category": "Engineering"},
    )
    record_b = await ctx.create_entry(
        track_id=track.id,
        entry_type_key="showcase_record",
        title="Record B",
        custom_fields={"status": "Active", "category": "Design"},
    )
    record_c = await ctx.create_entry(
        track_id=track.id,
        entry_type_key="showcase_record",
        title="Record C",
        custom_fields={"status": "Draft", "category": "Ops"},
    )
    assert record_a and record_b and record_c

    link_result = await demo_link_selected_records(
        {"entry_id": record_c.id, "linked_ids": [record_a.id, record_b.id]}, ctx
    )
    assert link_result["ok"] is True
    assert link_result["linked_count"] == 2

    reloaded = await ctx.get_entry_system(record_c.id)
    assert reloaded.custom_fields["linked_summary"] == "Record A, Record B"


@pytest.mark.asyncio
async def test_seed_demo_data_creates_a_real_tree_and_comments_and_is_idempotent():
    ws, owner_id = await _make_workspace()
    lib = await _load_library_cp("region-gallery")
    result = await install_app(
        workspace_id=ws.id, library_cp_id=lib.id, actor_id=owner_id
    )
    app_node = await App.get(result["app_id"])
    ctx = ToolContext(user_id=owner_id, workspace_id=ws.id, scope="test")

    from app.profiles.region_gallery.tools.seed_demo_data import (  # type: ignore[import]
        seed_demo_data,
    )

    first = await seed_demo_data({}, ctx)
    assert first["ok"] is True
    assert first["records_created"] == 13
    assert first["comments_created"] == 5

    # showcase_comment is a SECOND entry type on the SAME "Showcase
    # Records" track (see profile.yaml) — not a separate track, so both
    # entry types show up in one find_entries scan; split by shape.
    records_track = await _track_by_title(app_node, "Showcase Records")
    all_entries = await ctx.find_entries({"track_id": records_track.id})
    assert len(all_entries) == 18
    all_records = [e for e in all_entries if "status" in (e.custom_fields or {})]
    all_comments = [e for e in all_entries if "record" in (e.custom_fields or {})]
    assert len(all_records) == 13
    assert len(all_comments) == 5

    # A real parent/child link — "API Gateway Rollout" is a child of
    # "Platform Migration", wired via the real created entry id (not a
    # placeholder string), since seed_demo_data exists specifically
    # because declarative seeds can't do this.
    parent = next(r for r in all_records if r.title == "Platform Migration")
    child = next(r for r in all_records if r.title == "API Gateway Rollout")
    assert child.custom_fields.get("parent_record") == parent.id
    assert all(c.custom_fields.get("record") for c in all_comments)

    # Re-running must be a no-op, not a duplicate plant.
    second = await seed_demo_data({}, ctx)
    assert second["ok"] is True
    assert second["records_created"] == 0
    assert second["records_already_present"] == 13
    assert second["comments_created"] == 0
    assert second["comments_already_present"] == 5

    all_entries_again = await ctx.find_entries({"track_id": records_track.id})
    assert len(all_entries_again) == 18
