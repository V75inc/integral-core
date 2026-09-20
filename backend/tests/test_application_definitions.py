"""WP-04 contract tests for durable App definition revisions."""

import pytest

from app.services.application_definitions import (
    build_requirement_ledger,
    definition_fingerprint,
    preview_application_definition,
)


def test_requirement_ledger_names_supported_materialization_obligations():
    canonical = {
        "package": {"slug": "rental-ops"},
        "app": {
            "requires_apps": [
                {
                    "key": "identity",
                    "min_version": "2.0.0",
                    "optional": False,
                    "reason": "Renters are shared identities",
                }
            ],
            "tracks": [
                {
                    "key": "vehicles",
                    "name": "Vehicles",
                    "entry_types": [{"key": "car", "name": "Car"}],
                    "views": [{"key": "availability", "name": "Availability"}],
                }
            ],
            "track_templates": [{"key": "service", "name": "Service"}],
            "operations": [{"key": "mark_rented", "name": "Mark rented"}],
            "queries": [{"key": "due_service", "name": "Due service"}],
            "skills": [{"key": "rental_assistant", "name": "Rental assistant"}],
            "agents": [{"key": "ops", "name": "Operations"}],
        },
    }

    ledger = build_requirement_ledger(canonical)
    assert {item["id"] for item in ledger} == {
        "package:rental-ops",
        "dependency:identity",
        "track:vehicles",
        "entry_type:vehicles:car",
        "view:vehicles:availability",
        "track_template:service",
        "command:mark_rented",
        "query:due_service",
        "skill:rental_assistant",
        "agent:ops",
    }
    dependency = next(item for item in ledger if item["id"] == "dependency:identity")
    assert dependency["minimum_version"] == "2.0.0"
    assert dependency["required"] is True


def test_definition_fingerprint_is_order_insensitive_for_object_keys():
    left = {"package": {"slug": "rental"}, "app": {"tracks": []}}
    right = {"app": {"tracks": []}, "package": {"slug": "rental"}}
    assert definition_fingerprint(left) == definition_fingerprint(right)


def test_definition_preview_uses_business_labels_and_does_not_claim_zero_impact():
    before = {
        "content_profile_schema_version": 2,
        "scope": "app",
        "package": {"slug": "rental"},
        "app": {"tracks": [], "relations": [], "defaults": {}},
        "migrations": [],
    }
    candidate = {
        **before,
        "app": {
            "tracks": [{"key": "vehicles", "name": "Vehicles"}],
            "relations": [],
            "defaults": {},
            "operations": [{"key": "mark_rented", "name": "Mark rented"}],
        },
    }

    preview = preview_application_definition(
        before_manifest=before,
        candidate_manifest=candidate,
    )

    assert {change["label"] for change in preview["changes"]} == {
        "Vehicles",
        "Mark rented",
        "Feed",
    }
    assert {effect["subject_id"] for effect in preview["effects"]} == {
        "track:vehicles",
        "command:mark_rented",
        "view:vehicles:feed",
    }
    assert preview["affected_records"]["status"] == "not_evaluated"
    assert preview["affected_records"]["count"] is None


@pytest.mark.asyncio
async def test_blank_app_creation_binds_initial_local_definition():
    """Greenfield Apps use the same effective-definition seam as packages."""
    from app.models.edges import HAS_APPLICATION_DEFINITION, IS_MEMBER_OF
    from app.models.nodes import ApplicationDefinition, User
    from app.services.app_service import create_app_for_user
    from tests.fixtures.workspaces import make_org_workspace

    workspace = await make_org_workspace("definition-greenfield")
    owners = await workspace.nodes(
        edge=[IS_MEMBER_OF], direction="in", node=["User"], limit=1
    )
    owner = owners[0]
    assert isinstance(owner, User)

    app = await create_app_for_user(
        owner.id,
        "Greenfield Rentals",
        workspace_id=workspace.id,
    )

    definition = await ApplicationDefinition.get(app.active_definition_id)
    assert definition is not None
    assert definition.source_kind == "local"
    assert definition.source_profile_id
    attached = await app.nodes(
        edge=[HAS_APPLICATION_DEFINITION],
        direction="out",
        node=["ApplicationDefinition"],
    )
    assert [item.id for item in attached] == [definition.id]
