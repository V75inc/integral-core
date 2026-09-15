"""Projects bundle transform hook unit tests."""

from __future__ import annotations

import pytest

from app.services.hooks.declarative import apply_transform

pytestmark = pytest.mark.smoke


def test_opportunity_to_project_maps_value_to_budget():
    source = {
        "title": "Globex rollout",
        "body": "Won deal",
        "custom_fields": {
            "value": 250000,
            "contact": "contact-1",
            "stage": "won",
        },
    }
    block = {
        "copy_fields": [
            {"from": "title", "to": "title"},
            {"from": "body", "to": "body"},
            {"from": "custom_fields.contact", "to": "custom_fields.contact"},
            {"from": "custom_fields.value", "to": "custom_fields.budget"},
        ],
    }
    projected = apply_transform(source, block)
    assert projected["title"] == "Globex rollout"
    assert projected["custom_fields"]["budget"] == 250000
    assert "value" not in projected.get("custom_fields", {})


def test_proposal_to_project_maps_total_price_to_budget():
    source = {
        "title": "Contoso proposal",
        "body": "Accepted",
        "custom_fields": {
            "total_price": 185000,
            "total_cost": 120000,
            "status": "accepted",
        },
    }
    block = {
        "copy_fields": [
            {"from": "title", "to": "title"},
            {"from": "body", "to": "body"},
            {"from": "custom_fields.total_price", "to": "custom_fields.budget"},
        ],
        "strip_fields": [
            "custom_fields.total_cost",
        ],
    }
    projected = apply_transform(source, block)
    assert projected["custom_fields"]["budget"] == 185000
    assert "total_price" not in projected.get("custom_fields", {})
    assert "total_cost" not in projected.get("custom_fields", {})


def test_projects_profile_cross_app_contact_field():
    """Projects manifest declares CRM cross-app opt-in on project.contact."""
    from app.services.content_profile_loader import load_library_profiles

    spec = next(s for s in load_library_profiles() if s.slug == "projects")
    manifest = spec.manifest
    tracks = (manifest.get("app") or {}).get("tracks") or []
    projects_track = next(t for t in tracks if t.get("key") == "projects")
    project_et = next(
        et
        for et in projects_track.get("entry_types") or []
        if et.get("key") == "project"
    )
    contact_field = next(
        f for f in project_et.get("fields") or [] if f.get("key") == "contact"
    )
    relation = contact_field.get("relation") or {}
    assert relation.get("target_app") == "crm"
    assert relation.get("allow_cross_app") is True
