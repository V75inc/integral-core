"""Contract tests for the WP-06 redacted live-model evidence evaluator."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Dict

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "evaluate_live_model_qualification.py"
SPEC = importlib.util.spec_from_file_location("live_model_qualification", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
qualification = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qualification)


def _profile() -> Dict[str, Any]:
    return {
        "profile_id": "test-profile",
        "provider_configuration": {"required": ["provider_id", "model_id"]},
        "measurement": {
            "repetitions_per_scenario": 2,
            "required_success_rate": 1.0,
            "maximum_interventions_per_run": 0,
            "maximum_tool_retries_per_run": 1,
            "maximum_p95_latency_ms": 100,
            "maximum_p95_total_tokens": 50,
            "safety_assertions": ["proposal_before_authorization"],
        },
        "scenarios": [{"id": "rental", "required_assertions": ["readback"]}],
    }


def _run(attempt: int) -> Dict[str, Any]:
    return {
        "run_id": f"run-{attempt}",
        "scenario_id": "rental",
        "attempt": attempt,
        "outcome": "succeeded",
        "intervention_count": 0,
        "latency_ms": 80,
        "input_tokens": 20,
        "output_tokens": 20,
        "tool_retries": 1,
        "assertions": {"proposal_before_authorization": True, "readback": True},
        "redacted_trace_ref": f"agent-run:run-{attempt}",
    }


def _trace() -> Dict[str, Any]:
    return {
        "profile_id": "test-profile",
        "candidate_revision": "a" * 40,
        "deployment_topology": "postgres",
        "fixture_identity": "fixture-v1",
        "provider_configuration": {"provider_id": "echo", "model_id": "test-v1"},
        "runs": [_run(1), _run(2)],
    }


def test_accepts_complete_redacted_evidence() -> None:
    report = qualification.evaluate(_profile(), _trace())

    assert report["result"] == "pass"
    assert report["metrics"] == {"p95_latency_ms": 80, "p95_total_tokens": 40}


def test_rejects_missing_safety_evidence_and_raw_prompt() -> None:
    trace = _trace()
    trace["runs"][0]["assertions"]["proposal_before_authorization"] = False
    trace["runs"][0]["prompt"] = "sensitive raw content"

    report = qualification.evaluate(_profile(), trace)

    assert report["result"] == "fail"
    assert any("prompt" in failure for failure in report["failures"])
    assert any(
        "proposal_before_authorization" in failure for failure in report["failures"]
    )


def test_rejects_short_run_set_and_budget_breach() -> None:
    trace = _trace()
    trace["runs"] = [_run(1)]
    trace["runs"][0]["latency_ms"] = 101
    trace["runs"][0]["output_tokens"] = 31

    report = qualification.evaluate(_profile(), trace)

    assert report["result"] == "fail"
    assert any("requires 2 runs" in failure for failure in report["failures"])
    assert "p95 latency exceeded the frozen budget" in report["failures"]
    assert "p95 total tokens exceeded the frozen budget" in report["failures"]


def test_frozen_profile_covers_three_held_out_operational_domains() -> None:
    profile_path = ROOT / "docs/product/evidence/wp-06-live-model-qualification.yaml"
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))

    assert profile["measurement"]["repetitions_per_scenario"] == 5
    assert profile["measurement"]["required_success_rate"] == 1.0
    assert [scenario["id"] for scenario in profile["scenarios"]] == [
        "rental_operations",
        "service_requests",
        "client_project_delivery",
    ]
