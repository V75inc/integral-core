"""CREATE_ANCHOR_SENTINEL opt-in for auto_provision:false track relations."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from app.models.nodes import EntryType, Track
from app.services.operational_model_entry_fields import (
    CREATE_ANCHOR_SENTINEL,
    validate_and_materialize_entry_custom_fields,
)


def test_projects_profile_yaml_opt_in_financials_contracts():
    """Shipped projects YAML: Details auto; Financials/Contracts opt-in."""
    path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "packages"
        / "projects"
        / "operational-model.yaml"
    )
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    projects_track = next(
        t for t in manifest["app"]["tracks"] if t["key"] == "projects"
    )
    project_et = next(
        et for et in projects_track["entry_types"] if et["key"] == "project"
    )
    by_key = {f["key"]: f for f in project_et["fields"]}
    assert by_key["details_track"]["relation"]["auto_provision"] is True
    assert by_key["financials_track"]["relation"]["auto_provision"] is False
    assert by_key["contracts_track"]["relation"]["auto_provision"] is False


def test_projects_profile_yaml_declares_sprints_track():
    """Shipped projects YAML: Sprints sibling track + Task.sprint relation."""
    path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "packages"
        / "projects"
        / "operational-model.yaml"
    )
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    tracks = {t["key"]: t for t in manifest["app"]["tracks"]}
    assert "sprints" in tracks
    sprint_et = next(
        et for et in tracks["sprints"]["entry_types"] if et["key"] == "sprint"
    )
    sprint_fields = {f["key"]: f for f in sprint_et["fields"]}
    assert sprint_fields["status"]["enum"] == ["planned", "active", "done"]
    assert sprint_fields["project"]["relation"]["target_entry_types"] == ["project"]
    assert sprint_fields["project"]["relation"]["allow_cross_track"] is True

    details = next(
        t for t in manifest["app"]["track_templates"] if t["key"] == "project-details"
    )
    task = next(et for et in details["entry_types"] if et["key"] == "task")
    sprint_rel = next(f for f in task["fields"] if f["key"] == "sprint")
    assert sprint_rel["type"] == "relation"
    assert sprint_rel["relation"]["target_entry_types"] == ["sprint"]
    assert sprint_rel["relation"]["allow_cross_track"] is True
    view_keys = {v["key"] for v in details["views"]}
    assert "tasks-by-sprint" in view_keys

    project_et = next(
        et for et in tracks["projects"]["entry_types"] if et["key"] == "project"
    )
    related = {r["view"] for r in project_et.get("related_views") or []}
    assert ":anchored_track/tasks-by-sprint" in related


@pytest.mark.asyncio
async def test_create_anchor_sentinel_provisions_when_auto_provision_false(
    monkeypatch,
):
    """``__create_anchor__`` materializes even when auto_provision is false."""
    track = await Track.create(
        title="Projects",
        owner_id="anc-sentinel-user",
        workspace_id="ws-anc-sentinel",
    )
    et = await EntryType.create(
        name="Project",
        name_fold="project",
        track_id=track.id,
        form_schema={
            "fields": [
                {
                    "key": "financials_track",
                    "type": "relation",
                    "relation": {
                        "target": "track",
                        "target_track_template": "project-financials",
                        "auto_provision": False,
                    },
                }
            ]
        },
    )

    materialize = AsyncMock(return_value=SimpleNamespace(id="n.Track.fin-new"))
    reuse = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "app.services.operational_model_graph.materialize_anchor_track",
        materialize,
    )
    monkeypatch.setattr(
        "app.services.operational_model_graph._maybe_reuse_existing_anchor",
        reuse,
    )

    out, refs = await validate_and_materialize_entry_custom_fields(
        track=track,
        entry_type=et,
        custom_fields={"financials_track": CREATE_ANCHOR_SENTINEL},
        runtime_tier={},
        actor_user_id="anc-sentinel-user",
        source_entry_title="Demo Project",
    )

    assert out["financials_track"] == "n.Track.fin-new"
    assert refs and refs[0]["targets"] == ["n.Track.fin-new"]
    materialize.assert_awaited_once()
    assert materialize.await_args.kwargs["template_key"] == "project-financials"


@pytest.mark.asyncio
async def test_omitted_opt_in_anchor_stays_null(monkeypatch):
    """Omitted value + auto_provision false → field stays null (no materialize)."""
    track = await Track.create(
        title="Projects Omit",
        owner_id="anc-sentinel-user",
        workspace_id="ws-anc-sentinel",
    )
    et = await EntryType.create(
        name="Project",
        name_fold="project",
        track_id=track.id,
        form_schema={
            "fields": [
                {
                    "key": "contracts_track",
                    "type": "relation",
                    "relation": {
                        "target": "track",
                        "target_track_template": "contracts-legal",
                        "auto_provision": False,
                    },
                }
            ]
        },
    )

    materialize = AsyncMock(return_value=SimpleNamespace(id="n.Track.should-not"))
    monkeypatch.setattr(
        "app.services.operational_model_graph.materialize_anchor_track",
        materialize,
    )

    out, refs = await validate_and_materialize_entry_custom_fields(
        track=track,
        entry_type=et,
        custom_fields={},
        runtime_tier={},
        actor_user_id="anc-sentinel-user",
    )

    assert out.get("contracts_track") is None
    assert refs == []
    materialize.assert_not_awaited()
