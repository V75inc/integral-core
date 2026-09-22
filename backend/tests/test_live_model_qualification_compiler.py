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
            "tool_retries": 0,
            "assertions": {"proposal_before_authorization": True},
            "redacted_trace_ref": "agent-run:run-1",
            "run_ids": ["run-1"],
            "redacted_trace_refs": ["agent-run:run-1"],
        }
    ]
    assert "models" not in trace["runs"][0]


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
