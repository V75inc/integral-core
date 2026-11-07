"""Exemplar seed structural tests — Phase 3.1 Plan 03.1-05 Task 2 (ANC-11).

Verifies the shipped ``projects`` library Operational Model (YAML under
``projects/operational-model.yaml``) declares the Projects + Project-Details
exemplar that demonstrates the anchor pattern end-to-end:

  * App-scope manifest with ``app.track_templates[]`` declaring project-details
  * ``project`` entry type carries the ``details_track`` anchor field
  * Project-Details template has task, update and resource entry types and typed views

Set INTEGRAL_BUSINESS_APPS_ROOT to the Business integral-apps directory.
Loads through ``load_library_operational_models`` (same path as
``test_anchor_integration``) so regressions in the shipped profile fail here.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import pytest

from app.services.operational_model_loader import load_library_operational_models
from app.services.operational_model_runtime import compile_canonical_manifest

pytestmark = pytest.mark.domain_app


def _projects_manifest() -> Dict[str, Any]:
    root = Path(os.environ["INTEGRAL_BUSINESS_APPS_ROOT"])
    specs = {
        s.slug: s
        for s in load_library_operational_models(
            packages_root=root, verify_signatures=False, core_only=False
        )
    }
    spec = specs.get("projects")
    assert (
        spec is not None
    ), "the `projects` library Operational Model is missing from INTEGRAL_BUSINESS_APPS_ROOT"
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


def test_sprint_project_relation_targets_one_project():
    """A sprint belongs to one customer or internal project."""
    sprint = _sprint_entry_type(_projects_manifest())
    project = next(f for f in sprint["fields"] if f["key"] == "project")
    assert project["relation"]["target_entry_types"] == ["project"]
    assert set(project["relation"]["target_track_types"]) == {
        "projects",
        "internal-projects",
    }
    assert project["relation"]["many"] is False


def test_sprint_seed_links_contoso_project():
    """Manifest Contoso sprint seed references the Contoso project seed id."""
    manifest = _projects_manifest()
    seeds = manifest["app"]["seeds"]
    sprints = next(g for g in seeds if g.get("track") == "sprints")
    contoso = next(e for e in sprints["entries"] if e["id"] == "seed_sprint_contoso_1")
    assert contoso["custom_fields"]["project"] == "seed_project_contoso"


def test_project_entry_type_carries_anchor_field():
    """The ``project`` entry type carries the Phase 3.1 anchor field."""
    project_et = _project_entry_type(_projects_manifest())
    anchor_field = next(f for f in project_et["fields"] if f["key"] == "details_track")
    assert anchor_field["type"] == "relation"
    assert anchor_field["relation"]["target"] == "track"
    assert anchor_field["relation"]["target_track_template"] == "project-details"
    assert anchor_field["relation"]["auto_provision"] is True


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


def test_project_details_template_declares_expected_entry_types():
    """The Project-Details template declares the expected entry types."""
    template = _project_details_template(_projects_manifest())
    keys = {et["key"] for et in template["entry_types"]}
    assert keys == {"task", "update", "resource"}


def test_project_details_template_has_views_with_entry_type_keys_filter():
    """≥4 views, with per-entry-type filters via ``entry_type_keys``."""
    template = _project_details_template(_projects_manifest())
    views = template["views"]
    assert {v["key"]: v["entry_type_keys"] for v in views} == {
        "tasks-board": ["task"],
        "tasks-by-sprint": ["task"],
        "updates-feed": ["update"],
        "resources-gallery": ["resource"],
    }


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
    assert et_keys == {"task", "update", "resource"}
