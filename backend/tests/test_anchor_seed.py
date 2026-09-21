"""Exemplar seed structural tests — Phase 3.1 Plan 03.1-05 Task 2 (ANC-11).

Verifies the shipped ``projects`` library Operational Model (YAML under
``app/packages/projects/operational-model.yaml``) declares the Projects + Project-Details
exemplar that demonstrates the anchor pattern end-to-end:

  * App-scope manifest with ``app.track_templates[]`` declaring project-details
  * ``project`` entry type carries the ``details_track`` anchor field
  * Project-Details template has four mixed entry types + typed views

Loads through ``load_library_operational_models`` (same path as
``test_anchor_integration``) so regressions in the shipped profile fail here.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from app.services.operational_model_loader import load_library_operational_models
from app.services.operational_model_runtime import compile_canonical_manifest


def _projects_manifest() -> Dict[str, Any]:
    specs = {
        s.slug: s for s in load_library_operational_models(verify_signatures=False)
    }
    spec = specs.get("projects")
    assert (
        spec is not None
    ), "the `projects` library Operational Model is missing from app/packages/"
    return spec.manifest


def _project_entry_type(manifest: Dict[str, Any]) -> Dict[str, Any]:
    tracks = manifest["app"]["tracks"]
    projects_track = next(t for t in tracks if t["key"] == "projects")
    return next(et for et in projects_track["entry_types"] if et["key"] == "project")


def _project_details_template(manifest: Dict[str, Any]) -> Dict[str, Any]:
    tts = manifest["app"]["track_templates"]
    return next(t for t in tts if t["key"] == "project-details")


def test_projects_library_profile_loads():
    """YAML loader returns the projects package with expected sections."""
    manifest = _projects_manifest()
    assert manifest["scope"] == "app"
    assert "tracks" in manifest["app"]
    assert "track_templates" in manifest["app"]
    track_keys = {t["key"] for t in manifest["app"]["tracks"]}
    assert "projects" in track_keys
    assert "sprints" in track_keys


def test_task_entry_type_carries_sprint_relation():
    """Tasks on project-details reference Sprint entries cross-track."""
    template = _project_details_template(_projects_manifest())
    task = next(et for et in template["entry_types"] if et["key"] == "task")
    sprint = next(f for f in task["fields"] if f["key"] == "sprint")
    assert sprint["relation"]["target_entry_types"] == ["sprint"]
    assert sprint["relation"]["allow_cross_track"] is True
    assert "tasks-by-sprint" in {v["key"] for v in template["views"]}


def _sprint_entry_type(manifest: Dict[str, Any]) -> Dict[str, Any]:
    tracks = manifest["app"]["tracks"]
    sprints_track = next(t for t in tracks if t["key"] == "sprints")
    return next(et for et in sprints_track["entry_types"] if et["key"] == "sprint")


def test_sprint_project_relation_is_many():
    """Sprint.project is multi-select so one sprint can span projects."""
    sprint = _sprint_entry_type(_projects_manifest())
    project = next(f for f in sprint["fields"] if f["key"] == "project")
    assert project["name"] == "Projects"
    assert project["relation"]["many"] is True


def test_sprint_declares_tasks_ui_field():
    """Sprint.tasks is declared for membership UI (Task.sprint remains SoT)."""
    sprint = _sprint_entry_type(_projects_manifest())
    tasks = next(f for f in sprint["fields"] if f["key"] == "tasks")
    assert tasks["relation"]["many"] is True
    assert tasks["relation"]["target_entry_types"] == ["task"]
    assert "project-details" in tasks["relation"]["target_track_types"]


def test_sprint_seed_links_contoso_project():
    """Manifest Contoso sprint seed references the Contoso project seed id."""
    manifest = _projects_manifest()
    seeds = manifest["app"]["seeds"]
    sprints = next(g for g in seeds if g.get("track") == "sprints")
    contoso = next(e for e in sprints["entries"] if e["id"] == "seed_sprint_contoso_1")
    assert contoso["custom_fields"]["project"] == ["seed_project_contoso"]


def test_project_entry_type_carries_anchor_field():
    """The ``project`` entry type carries the Phase 3.1 anchor field."""
    project_et = _project_entry_type(_projects_manifest())
    anchor_field = next(f for f in project_et["fields"] if f["key"] == "details_track")
    assert anchor_field["type"] == "relation"
    assert anchor_field["relation"]["target"] == "track"
    assert anchor_field["relation"]["target_track_template"] == "project-details"
    assert anchor_field["relation"]["auto_provision"] is True


def test_financials_and_contracts_are_opt_in_anchors():
    """Financials / Contracts require CREATE_ANCHOR_SENTINEL; Details auto-provisions."""
    project_et = _project_entry_type(_projects_manifest())
    by_key = {f["key"]: f for f in project_et["fields"]}
    assert by_key["financials_track"]["relation"]["auto_provision"] is False
    assert by_key["contracts_track"]["relation"]["auto_provision"] is False
    assert by_key["details_track"]["relation"]["auto_provision"] is True
    assert (
        by_key["financials_track"]["relation"]["target_track_template"]
        == "project-financials"
    )
    assert (
        by_key["contracts_track"]["relation"]["target_track_template"]
        == "contracts-legal"
    )


def test_project_anchor_field_governance_block_valid():
    """The anchor field governance block has valid values per ANC-03."""
    project_et = _project_entry_type(_projects_manifest())
    anchor_field = next(f for f in project_et["fields"] if f["key"] == "details_track")
    gov = anchor_field["relation"]["governance"]
    assert gov["cardinality"] == "one"
    assert gov["cascade"] == "hard"
    assert gov["acl_inheritance"] == "inherit"


def test_app_declares_project_details_track_template():
    """``app.track_templates[]`` includes the project-details template."""
    manifest = _projects_manifest()
    keys = {t["key"] for t in manifest["app"]["track_templates"]}
    assert "project-details" in keys


def test_project_details_template_has_four_entry_types():
    """The Project-Details template declares all four mixed entry types."""
    template = _project_details_template(_projects_manifest())
    keys = {et["key"] for et in template["entry_types"]}
    assert keys == {"task", "activity", "update", "resource"}


def test_project_details_template_has_views_with_entry_type_keys_filter():
    """≥4 views, with per-entry-type filters via ``entry_type_keys``."""
    template = _project_details_template(_projects_manifest())
    views = template["views"]
    assert len(views) >= 4
    typed_views = [v for v in views if v.get("entry_type_keys")]
    assert len(typed_views) >= 4
    composable_views = [v for v in views if v.get("view_type") == "composable_list"]
    assert composable_views, "Expected at least one composable_list mixed-mode view"


def test_project_entry_type_has_related_views():
    """The Project entry type declares ≥2 related_views, :anchored_track-prefixed."""
    project_et = _project_entry_type(_projects_manifest())
    rv = project_et.get("related_views", [])
    assert len(rv) >= 2
    assert all(r["view"].startswith(":anchored_track/") for r in rv)


def test_projects_manifest_compiles():
    """Shipped projects manifest compiles through the substrate validator."""
    compiled = compile_canonical_manifest(manifest=_projects_manifest())
    assert compiled["scope"] == "app"
    project_et = _project_entry_type(compiled)
    anchor_field = next(f for f in project_et["fields"] if f["key"] == "details_track")
    rel = anchor_field["relation"]
    assert rel["target"] == "track"
    assert rel["target_track_template"] == "project-details"
    assert rel["auto_provision"] is True
    assert rel["governance"]["cardinality"] == "one"
    tts = compiled["app"]["track_templates"]
    project_details = next(t for t in tts if t["key"] == "project-details")
    et_keys = {et["key"] for et in project_details["entry_types"]}
    assert et_keys == {"task", "activity", "update", "resource"}
