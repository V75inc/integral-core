"""New AI-authored containers require meaningful, approval-bound descriptions."""

import pytest

from app.agentive.tooling.bindings import (
    _stage_create_app,
    _stage_create_track,
    _stage_register_track_template,
)
from app.schemas.creation_description import creation_description
from app.schemas.design_blueprint import DesignBlueprint
from app.services.design_blueprint import plan_fidelity_errors, validate_blueprint

pytestmark = pytest.mark.smoke

DESCRIPTION = "Stores customer contact details so staff can coordinate repair bookings."


@pytest.mark.parametrize(
    "value", [None, "", "  ", "...", "TBD", "TODO", "Customers", "customers."]
)
@pytest.mark.parametrize("kind", ["app", "track", "template"])
def test_new_container_refuses_missing_or_placeholder_description(kind, value):
    args = {"name": "Customers", "description": value}
    if kind == "template":
        args.update(app_id="app-1", entry_types=[{"name": "Customer", "fields": []}])
    stager = {
        "app": _stage_create_app,
        "track": _stage_create_track,
        "template": _stage_register_track_template,
    }[kind]
    with pytest.raises(ValueError, match="meaningful description"):
        stager(args)


def test_description_is_normalized_and_retained_in_preview_and_payload():
    result = _stage_create_track(
        {"name": "Customers", "description": f"  {DESCRIPTION}  "}
    )
    assert result["payload"]["description"] == DESCRIPTION
    assert DESCRIPTION in result["diff_human"]
    assert creation_description("保存客户联系方式，帮助安排维修预约。", "客户")


def test_legacy_blueprint_remains_readable_but_new_proposal_requires_description():
    legacy = {"app": {"id": "app", "name": "Customers"}, "tracks": []}
    assert DesignBlueprint.model_validate(legacy).app.description is None
    result, error = validate_blueprint(legacy)
    assert result is None
    assert "meaningful description" in error
    legacy["app"]["description"] = DESCRIPTION
    result, error = validate_blueprint(legacy)
    assert error is None
    assert result["app"]["description"] == DESCRIPTION


def test_plan_must_keep_approved_description():
    blueprint = DesignBlueprint.model_validate(
        {
            "app": {"id": "app", "name": "Customers", "description": DESCRIPTION},
            "tracks": [],
        }
    ).model_dump(mode="json")
    operations = [
        (
            "integral_create_app",
            {"name": "Customers", "description": "An unrelated purpose."},
        )
    ]
    assert any(
        "approved description" in error
        for error in plan_fidelity_errors(blueprint, operations, new_app=True)
    )
    operations[0][1]["description"] = DESCRIPTION
    assert plan_fidelity_errors(blueprint, operations, new_app=True) == []


def test_package_new_tracks_require_descriptions_but_legacy_tracks_remain_operable():
    from app.schemas.creation_description import validate_new_package_track_descriptions

    manifest = {
        "app": {
            "tracks": [
                {"key": "legacy", "name": "Legacy"},
                {"key": "customers", "name": "Customers"},
            ]
        }
    }
    with pytest.raises(ValueError, match="Customers"):
        validate_new_package_track_descriptions(manifest, {"legacy"})
    manifest["app"]["tracks"][1]["description"] = DESCRIPTION
    validate_new_package_track_descriptions(manifest, {"legacy"})
    # Optional templates are not materialized as a side effect of installation.
    manifest["app"]["tracks"][1].pop("description")
    manifest["app"]["tracks"][1]["provision_on_create"] = False
    validate_new_package_track_descriptions(manifest, {"legacy"})


def test_package_provisioning_disabled_does_not_require_uncreated_tracks():
    from app.schemas.creation_description import validate_new_package_track_descriptions

    manifest = {
        "app": {
            "defaults": {"provision_prescribed_tracks": False},
            "tracks": [{"key": "records", "name": "Records"}],
        }
    }
    validate_new_package_track_descriptions(manifest, set(), set())
    manifest["app"]["track_templates"] = [{"key": "notes", "name": "Notes"}]
    with pytest.raises(ValueError, match="Notes"):
        validate_new_package_track_descriptions(manifest, set(), set())
