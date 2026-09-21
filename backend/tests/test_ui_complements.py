"""Compiler contract tests for declarative UI Complement Recipes."""

from app.exceptions import OperationalModelValidationError
from app.services.operational_model_compile import compile_canonical_manifest


def test_ui_complements_round_trip_through_canonical_compile():
    compiled = compile_canonical_manifest(
        manifest={
            "operational_model_schema_version": 2,
            "scope": "track",
            "ui_complements": [
                {
                    "id": "operational-record",
                    "version": ">=1.0",
                    "track": "pay_runs",
                    "entry_type": "pay_run",
                    "config": {"summary_view": "pay_run_summary"},
                }
            ],
            "track": {
                "key": "pay_runs",
                "entry_types": [{"key": "pay_run", "name": "Pay Run"}],
                "views": [
                    {
                        "key": "pay_run_summary",
                        "view_type": "summary_tiles",
                        "entry_type_keys": ["pay_run"],
                        "config": {"tiles": []},
                    }
                ],
            },
        }
    )

    assert compiled["ui_complements"] == [
        {
            "id": "operational-record",
            "version": ">=1.0",
            "track": "pay_runs",
            "entry_type": "pay_run",
            "config": {"summary_view": "pay_run_summary"},
        }
    ]
    generated = next(
        view
        for view in compiled["track"]["views"]
        if view["key"] == "pay_run_operational_record"
    )
    assert generated["key"] == "pay_run_operational_record"
    assert generated["view_type"] == "layout_container"
    assert compiled["track"]["entry_types"][0]["related_views"] == [
        {
            "view": "pay_run_operational_record",
            "position": "primary",
            "bind": {},
        }
    ]


def test_ui_complements_require_a_target():
    try:
        compile_canonical_manifest(
            manifest={
                "operational_model_schema_version": 2,
                "scope": "track",
                "ui_complements": [{"id": "operational-record"}],
                "track": {"entry_types": [], "views": []},
            }
        )
    except OperationalModelValidationError as exc:
        assert "target a track or entry_type" in str(exc)
    else:
        raise AssertionError("un-targeted UI complement should be rejected")


def test_ui_complements_reject_duplicate_targets():
    try:
        compile_canonical_manifest(
            manifest={
                "operational_model_schema_version": 2,
                "scope": "track",
                "ui_complements": [
                    {"id": "operational-record", "track": "pay_runs"},
                    {"id": "operational-record", "track": "pay_runs"},
                ],
                "track": {
                    "key": "pay_runs",
                    "entry_types": [{"key": "pay_run", "name": "Pay Run"}],
                    "views": [],
                },
            }
        )
    except OperationalModelValidationError as exc:
        assert "duplicate recipe target" in str(exc)
    else:
        raise AssertionError("duplicate UI complement target should be rejected")


def test_guided_workflow_complement_populates_existing_wizard_contract():
    compiled = compile_canonical_manifest(
        manifest={
            "operational_model_schema_version": 2,
            "scope": "track",
            "ui_complements": [
                {
                    "id": "guided-workflow",
                    "track": "records",
                    "entry_type": "record",
                    "config": {
                        "steps": [{"kind": "summary", "key": "review"}],
                        "on_create_tool": "after_create",
                    },
                }
            ],
            "track": {
                "key": "records",
                "entry_types": [{"key": "record", "name": "Record"}],
                "views": [],
            },
        }
    )

    wizard = compiled["track"]["entry_types"][0]["create_wizard"]
    assert wizard["steps"] == [{"kind": "summary", "key": "review", "title": ""}]
    assert wizard["on_create_tool"] == "after_create"
