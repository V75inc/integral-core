"""Integrity checks for the Phase 0 qualification scenario inventory."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.unit
def test_phase0_manifest_covers_domains_negative_cases_and_evidence() -> None:
    yaml = pytest.importorskip("yaml")
    manifest_path = (
        Path(__file__).resolve().parents[2]
        / "docs/product/evidence/phase-0-qualification-manifest.yaml"
    )
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))

    assert manifest["schema_version"] == 1
    fixtures = {fixture["id"]: fixture for fixture in manifest["fixtures"]}
    assert set(fixtures) == {
        "rental_operations",
        "equipment_checkout",
        "client_project_delivery",
        "stock_replenishment",
    }
    for fixture in fixtures.values():
        assert fixture["decoys"]
        assert fixture["required_cases"]

    assertion_ids = {assertion["id"] for assertion in manifest["assertions"]}
    assert {
        "fields_are_explicit",
        "rendered_projections_agree",
        "effects_are_singular",
        "recovery_is_honest",
        "scope_is_enforced",
        "lifecycle_is_durable",
    } <= assertion_ids
    assert {
        "git_revision",
        "command",
        "result",
        "retained_log",
    } <= set(manifest["evidence"]["required_for_each_run"])
