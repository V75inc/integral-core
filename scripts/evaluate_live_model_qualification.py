#!/usr/bin/env python3
"""Evaluate redacted resident live-model traces against a frozen WP-06 profile.

The runner deliberately does not call a model or keep prompts/completions. A
live runner exports redacted references to durable ``AgentRun`` records, then
this program verifies coverage, safety assertions, and fixed quality budgets.
It exits non-zero for incomplete evidence so an attractive one-off demo cannot
be recorded as qualification.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

import yaml


FORBIDDEN_TRACE_KEYS = frozenset(
    {
        "prompt",
        "completion",
        "messages",
        "api_key",
        "authorization",
        "tool_observations",
    }
)


def _load_mapping(path: Path) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a mapping")
    return value


def _percentile_95(values: Sequence[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return ordered[index]


def _find_forbidden_keys(value: Any, path: str = "$") -> List[str]:
    found: List[str] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            nested_path = f"{path}.{key_text}"
            if key_text.lower() in FORBIDDEN_TRACE_KEYS:
                found.append(nested_path)
            found.extend(_find_forbidden_keys(nested, nested_path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found.extend(_find_forbidden_keys(nested, f"{path}[{index}]"))
    return found


def _required_strings(record: Mapping[str, Any], names: Iterable[str], label: str) -> List[str]:
    failures: List[str] = []
    for name in names:
        if not isinstance(record.get(name), str) or not str(record[name]).strip():
            failures.append(f"{label}.{name} is required")
    return failures


def evaluate(profile: Mapping[str, Any], trace: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a redacted qualification report without mutating any evidence."""
    failures: List[str] = []
    failures.extend(_find_forbidden_keys(trace))

    if trace.get("profile_id") != profile.get("profile_id"):
        failures.append("trace.profile_id does not match the frozen profile")
    failures.extend(
        _required_strings(
            trace,
            ("candidate_revision", "deployment_topology", "fixture_identity"),
            "trace",
        )
    )
    provider = trace.get("provider_configuration")
    if not isinstance(provider, Mapping):
        failures.append("trace.provider_configuration is required")
    else:
        failures.extend(
            _required_strings(
                provider,
                profile.get("provider_configuration", {}).get("required", []),
                "trace.provider_configuration",
            )
        )

    runs = trace.get("runs")
    if not isinstance(runs, list):
        failures.append("trace.runs must be a list")
        runs = []
    measurement = profile.get("measurement", {})
    repetitions = int(measurement.get("repetitions_per_scenario", 0))
    success_target = float(measurement.get("required_success_rate", 1.0))
    max_interventions = int(measurement.get("maximum_interventions_per_run", 0))
    max_retries = int(measurement.get("maximum_tool_retries_per_run", 0))
    safety_assertions = list(measurement.get("safety_assertions", []))
    scenario_rows: List[Dict[str, Any]] = []
    all_latencies: List[int] = []
    all_tokens: List[int] = []

    for scenario in profile.get("scenarios", []):
        scenario_id = scenario.get("id")
        matching = [run for run in runs if isinstance(run, Mapping) and run.get("scenario_id") == scenario_id]
        successful = 0
        required_assertions = set(scenario.get("required_assertions", [])) | set(safety_assertions)
        for run in matching:
            run_label = f"run[{scenario_id}:{run.get('attempt', '?')}]"
            failures.extend(
                _required_strings(
                    run,
                    ("run_id", "scenario_id", "outcome", "redacted_trace_ref"),
                    run_label,
                )
            )
            assertions = run.get("assertions")
            if not isinstance(assertions, Mapping):
                failures.append(f"{run_label}.assertions must be a mapping")
                assertions = {}
            for assertion in required_assertions:
                if assertions.get(assertion) is not True:
                    failures.append(f"{run_label} did not prove {assertion}")
            try:
                intervention_count = int(run.get("intervention_count"))
                tool_retries = int(run.get("tool_retries"))
                latency_ms = int(run.get("latency_ms"))
                tokens = int(run.get("input_tokens")) + int(run.get("output_tokens"))
            except (TypeError, ValueError):
                failures.append(f"{run_label} has invalid numeric measurements")
                continue
            if intervention_count > max_interventions:
                failures.append(f"{run_label} exceeded intervention budget")
            if tool_retries > max_retries:
                failures.append(f"{run_label} exceeded tool retry budget")
            all_latencies.append(latency_ms)
            all_tokens.append(tokens)
            if run.get("outcome") == "succeeded":
                successful += 1
        success_rate = successful / len(matching) if matching else 0.0
        if len(matching) != repetitions:
            failures.append(f"{scenario_id} requires {repetitions} runs; received {len(matching)}")
        if success_rate < success_target:
            failures.append(f"{scenario_id} success rate {success_rate:.2f} is below {success_target:.2f}")
        scenario_rows.append(
            {
                "scenario_id": scenario_id,
                "runs": len(matching),
                "successful_runs": successful,
                "success_rate": success_rate,
            }
        )

    p95_latency = _percentile_95(all_latencies)
    p95_tokens = _percentile_95(all_tokens)
    if p95_latency > int(measurement.get("maximum_p95_latency_ms", 0)):
        failures.append("p95 latency exceeded the frozen budget")
    if p95_tokens > int(measurement.get("maximum_p95_total_tokens", 0)):
        failures.append("p95 total tokens exceeded the frozen budget")
    return {
        "schema_version": 1,
        "profile_id": profile.get("profile_id"),
        "candidate_revision": trace.get("candidate_revision"),
        "result": "pass" if not failures else "fail",
        "scenario_results": scenario_rows,
        "metrics": {"p95_latency_ms": p95_latency, "p95_total_tokens": p95_tokens},
        "failures": failures,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    profile = _load_mapping(args.profile)
    trace = _load_mapping(args.trace)
    report = evaluate(profile, trace)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
