"""WP-04 contract tests for durable App definition revisions."""

from app.services.application_definitions import (
    build_requirement_ledger,
    definition_fingerprint,
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
