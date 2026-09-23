"""Tests for compiling redacted resident qualification evidence."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Dict

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "compile_live_model_qualification.py"
SPEC = importlib.util.spec_from_file_location(
    "live_model_qualification_compiler", SCRIPT
)
assert SPEC is not None and SPEC.loader is not None
compiler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compiler)


def _profile() -> Dict[str, Any]:
    return {
        "profile_id": "resident-v1",
        "provider_configuration": {
            "required": [
                "provider_id",
                "model_id",
                "harness_binding",
                "credential_mode",
                "configuration_digest",
            ]
        },
    }


def _manifest() -> Dict[str, Any]:
    return {
        "profile_id": "resident-v1",
        "candidate_revision": "a" * 40,
        "deployment_topology": "local-postgres",
        "fixture_identity": "resident-fixtures-v1",
        "provider_configuration": {
            "provider_id": "openai",
            "model_id": "gpt-test",
            "harness_binding": "embedded-jvagent",
            "credential_mode": "environment",
            "configuration_digest": "b" * 64,
        },
        "runs": [
            {
                "scenario_id": "rental_operations",
                "attempt": 1,
                "intervention_count": 0,
                "assertions": {"proposal_before_authorization": True},
                "run_export": {
                    "run_id": "run-1",
                    "status": "succeeded",
                    "metrics": {
                        "latency_ms": 120,
                        "input_tokens": 40,
                        "output_tokens": 20,
                        "peak_input_tokens": 25,
                        "tool_retries": 0,
                    },
                    "models": [{"model_id": "gpt-test", "calls": 1}],
                    "redacted_trace_ref": "agent-run:run-1",
                },
            }
        ],
    }


def test_compiles_only_the_evaluator_safe_projection() -> None:
    """The trace contains only evaluator inputs derived from safe receipts."""
    trace = compiler.compile_trace(_profile(), _manifest())

    assert trace["runs"] == [
        {
            "run_id": "run-1",
            "scenario_id": "rental_operations",
            "attempt": 1,
            "outcome": "succeeded",
            "intervention_count": 0,
            "latency_ms": 120,
            "input_tokens": 40,
            "output_tokens": 20,
            "peak_input_tokens": 25,
            "tool_retries": 0,
            "assertions": {
                "correction_is_distinct_from_retry": False,
                "no_duplicate_approval": True,
                "no_fictitious_completion": False,
                "proposal_before_authorization": False,
                "receipt_backed_completion": False,
                "scoped_readback": False,
                "single_authorized_build": False,
            },
            "redacted_trace_ref": "agent-run:run-1",
            "run_ids": ["run-1"],
            "redacted_trace_refs": ["agent-run:run-1"],
        }
    ]
    assert "models" not in trace["runs"][0]


def test_caller_assertion_flags_are_not_proof() -> None:
    manifest = _manifest()
    manifest["runs"][0]["assertions"] = {
        "proposal_before_authorization": True,
        "single_authorized_build": True,
        "dashboard_materialized": True,
        "materialized_surface_matches_design": True,
    }

    trace = compiler.compile_trace(_profile(), manifest)
    assertions = trace["runs"][0]["assertions"]
    assert assertions["proposal_before_authorization"] is False
    assert assertions["single_authorized_build"] is False
    assert "dashboard_materialized" not in assertions
    assert "materialized_surface_matches_design" not in assertions


def test_reused_run_id_across_the_manifest_is_rejected() -> None:
    manifest = _manifest()
    second = dict(manifest["runs"][0])
    manifest["runs"].append(second)
    with pytest.raises(ValueError, match="repeats a run receipt"):
        compiler.compile_trace(_profile(), manifest)


def test_two_successful_builds_are_not_a_single_authorized_build() -> None:
    manifest = _manifest()
    first = manifest["runs"][0].pop("run_export")
    first["steps"] = [
        {"name": "integral_propose_design", "status": "succeeded"},
        {"name": "integral_build_approved_design", "status": "succeeded"},
        {"name": "integral_build_approved_design", "status": "succeeded"},
    ]
    second = {
        **first,
        "run_id": "run-2",
        "redacted_trace_ref": "agent-run:run-2",
        "steps": [{"name": "integral_commit_batch", "status": "succeeded"}],
    }
    manifest["runs"][0]["run_exports"] = [first, second]

    assertions = compiler.compile_trace(_profile(), manifest)["runs"][0]["assertions"]
    assert assertions["single_authorized_build"] is False
    assert assertions["receipt_backed_completion"] is True
    assert assertions["no_duplicate_approval"] is True


def _surface() -> Dict[str, Any]:
    return {
        "app_count": 1,
        "tracks": [
            {
                "name": "Cars",
                "fields": [
                    {"key": "name", "label": "Name", "type": "text"},
                    {"key": "status", "label": "Status", "type": "select"},
                    {"key": "owner", "label": "Owner", "type": "relation"},
                    {"key": "due", "label": "Milestone", "type": "date"},
                ],
                "views": ["feed", "table"],
            }
        ],
        "dashboards": [{"widget_count": 2}],
        "entries": [
            {"id": "e1", "values": {"status": "available"}},
            {"id": "e2", "values": {"status": "rented"}},
        ],
        "query": {
            "field": "status",
            "equals": "available",
            "rendered_ids": ["e1"],
        },
        "schema": {
            "revision_before": 1,
            "revision_after": 2,
            "ids_before": ["e1", "e2"],
            "ids_after": ["e1", "e2"],
        },
    }


def test_surface_snapshot_proves_materialized_checks() -> None:
    manifest = _manifest()
    manifest["runs"][0]["assertions"] = {
        "dashboard_materialized": False,
        "fields_and_status_materialized": False,
    }
    manifest["runs"][0]["surface"] = _surface()
    design = manifest["runs"][0].pop("run_export")
    design["steps"] = [{"name": "integral_propose_design", "status": "succeeded"}]
    build = {
        **design,
        "run_id": "run-2",
        "redacted_trace_ref": "agent-run:run-2",
        "steps": [{"name": "integral_build_approved_design", "status": "succeeded"}],
    }
    query = {
        **design,
        "run_id": "run-3",
        "redacted_trace_ref": "agent-run:run-3",
        "steps": [{"name": "integral_query", "status": "succeeded"}],
    }
    update = {
        **design,
        "run_id": "run-4",
        "redacted_trace_ref": "agent-run:run-4",
        "steps": [{"name": "integral_update_entry", "status": "succeeded"}],
    }
    manifest["runs"][0]["run_exports"] = [design, build, query, update]

    assertions = compiler.compile_trace(_profile(), manifest)["runs"][0]["assertions"]
    assert assertions["fields_and_status_materialized"] is True
    assert assertions["workflow_and_assignment_materialized"] is True
    assert assertions["relation_and_milestone_materialized"] is True
    assert assertions["dashboard_materialized"] is True
    assert assertions["exact_query_matches_view"] is True
    assert assertions["schema_evolution_preserves_records"] is True
    assert assertions["materialized_surface_matches_design"] is True
    assert assertions["scoped_readback"] is True
    assert assertions["correction_is_distinct_from_retry"] is True


def test_query_mismatch_and_lost_record_fail_closed() -> None:
    manifest = _manifest()
    surface = _surface()
    surface["query"]["rendered_ids"] = ["e1", "e2"]
    surface["schema"]["ids_after"] = ["e2"]
    surface["dashboards"] = [{"widget_count": 0}]
    manifest["runs"][0]["surface"] = surface

    assertions = compiler.compile_trace(_profile(), manifest)["runs"][0]["assertions"]
    assert assertions["exact_query_matches_view"] is False
    assert assertions["schema_evolution_preserves_records"] is False
    assert assertions["dashboard_materialized"] is False


def test_api_bundle_normalizes_to_a_compiler_snapshot() -> None:
    import importlib.util

    script = ROOT / "scripts" / "observe_qualification_surface.py"
    spec = importlib.util.spec_from_file_location("observe_surface", script)
    assert spec is not None and spec.loader is not None
    observer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(observer)
    snapshot = observer.surface_from_api_bundle(
        {
            "apps": {"apps": [{"id": "app-1", "name": "Rentals"}]},
            "tracks": [
                {
                    "name": "Cars",
                    "operational_model": {
                        "manifest": {
                            "entry_types": [
                                {
                                    "fields": [
                                        {
                                            "key": "status",
                                            "label": "Status",
                                            "type": "select",
                                        }
                                    ]
                                }
                            ],
                            "views": [{"type": "feed"}, {"view_type": "table"}],
                            "track": {
                                "entry_types": [
                                    {
                                        "fields": [
                                            {
                                                "key": "status",
                                                "label": "Status",
                                                "type": "select",
                                            }
                                        ]
                                    }
                                ],
                                "views": [{"type": "calendar"}],
                            },
                        }
                    },
                }
            ],
            "dashboards": {"dashboards": [{"widgets": [{}, {}]}]},
            "entries": {"entries": [{"id": "e1", "data": {"status": "available"}}]},
            "query": {"field": "status", "equals": "available", "rendered_ids": ["e1"]},
            "schema": {
                "revision_before": 1,
                "revision_after": 2,
                "ids_before": ["e1"],
                "ids_after": ["e1"],
            },
        }
    )
    assert snapshot["app_count"] == 1
    assert snapshot["tracks"][0]["fields"][0]["key"] == "status"
    assert snapshot["tracks"][0]["views"] == ["calendar"]
    assert snapshot["dashboards"] == [{"widget_count": 2}]
    assert snapshot["entries"] == [{"id": "e1", "values": {"status": "available"}}]


def test_missing_peak_input_fails_closed() -> None:
    manifest = _manifest()
    del manifest["runs"][0]["run_export"]["metrics"]["peak_input_tokens"]
    with pytest.raises(ValueError, match="peak_input_tokens was not measured"):
        compiler.compile_trace(_profile(), manifest)


def test_rejects_raw_content_even_when_nested_in_a_receipt() -> None:
    """Nested raw-payload keys cannot enter retained evidence."""
    manifest = _manifest()
    export = manifest["runs"][0]["run_export"]
    export["steps"] = [{"tool_observations": "secret"}]

    with pytest.raises(ValueError, match="forbidden key"):
        compiler.compile_trace(_profile(), manifest)


def test_rejects_model_configuration_mismatch() -> None:
    """Evidence cannot silently blend different model versions."""
    manifest = _manifest()
    manifest["runs"][0]["run_export"]["models"][0]["model_id"] = "other-model"

    with pytest.raises(ValueError, match="does not match configured model"):
        compiler.compile_trace(_profile(), manifest)


def test_compiles_whole_journey_and_proves_proposal_before_build() -> None:
    """A design and build span turns; budgets cover their combined cost."""
    manifest = _manifest()
    design = manifest["runs"][0]["run_export"]
    design["steps"] = [
        {
            "name": "integral_propose_design",
            "status": "succeeded",
        }
    ]
    build = {
        **design,
        "run_id": "run-2",
        "redacted_trace_ref": "agent-run:run-2",
        "steps": [{"name": "integral_commit_batch", "status": "succeeded"}],
    }
    manifest["runs"][0].pop("run_export")
    manifest["runs"][0]["run_exports"] = [design, build]

    trace = compiler.compile_trace(_profile(), manifest)
    row = trace["runs"][0]
    assert row["run_ids"] == ["run-1", "run-2"]
    assert row["input_tokens"] == 80
    assert row["output_tokens"] == 40
    assert row["assertions"]["proposal_before_authorization"] is True
    assert row["assertions"]["single_authorized_build"] is True
    assert row["assertions"]["receipt_backed_completion"] is True
    assert row["assertions"]["no_fictitious_completion"] is True

    manifest["runs"][0]["run_exports"].reverse()
    reversed_trace = compiler.compile_trace(_profile(), manifest)
    assertions = reversed_trace["runs"][0]["assertions"]
    assert assertions["proposal_before_authorization"] is False


def test_build_profile_refuses_design_only_evidence() -> None:
    """A single proposal turn cannot satisfy a full build scenario."""
    profile = _profile()
    profile["scenarios"] = [
        {
            "id": "rental_operations",
            "required_assertions": ["single_authorized_build"],
        }
    ]
    with pytest.raises(ValueError, match="requires run_exports"):
        compiler.compile_trace(profile, _manifest())


def test_compiler_accepts_one_call_approved_build_receipt() -> None:
    manifest = _manifest()
    proposal = manifest["runs"][0].pop("run_export")
    proposal["steps"] = [{"name": "integral_propose_design", "status": "succeeded"}]
    build = {
        **proposal,
        "run_id": "run-2",
        "redacted_trace_ref": "agent-run:run-2",
        "steps": [{"name": "integral_build_approved_design", "status": "succeeded"}],
    }
    manifest["runs"][0]["run_exports"] = [proposal, build]

    trace = compiler.compile_trace(_profile(), manifest)
    assert trace["runs"][0]["assertions"]["proposal_before_authorization"]
