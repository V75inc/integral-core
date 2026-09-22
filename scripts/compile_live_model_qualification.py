#!/usr/bin/env python3
"""Compile redacted resident receipts into a WP-06 qualification trace.

The resident API exports content-free AgentRun receipts.  This utility combines
those receipts with independently observed assertions into the exact redacted
input accepted by ``evaluate_live_model_qualification.py``. It intentionally
never calls a provider or records chat/model/tool payloads.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

import yaml

FORBIDDEN_KEYS = frozenset(
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


def _forbidden_path(value: Any, path: str = "$") -> str | None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            nested_path = f"{path}.{key}"
            if str(key).lower() in FORBIDDEN_KEYS:
                return nested_path
            found = _forbidden_path(nested, nested_path)
            if found:
                return found
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found = _forbidden_path(nested, f"{path}[{index}]")
            if found:
                return found
    return None


def _required_string(value: Mapping[str, Any], key: str, label: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{label}.{key} is required")
    return item


def _model_id(export: Mapping[str, Any]) -> str:
    models = export.get("models")
    if not isinstance(models, list) or not models:
        raise ValueError("run export must include at least one observed model")
    ids = sorted(
        {
            str(model.get("model_id") or "").strip()
            for model in models
            if isinstance(model, Mapping)
        }
    )
    ids = [model_id for model_id in ids if model_id]
    if len(ids) != 1:
        raise ValueError("run export must identify exactly one observed model")
    return ids[0]


def compile_trace(
    profile: Mapping[str, Any], manifest: Mapping[str, Any]
) -> Dict[str, Any]:
    """Build an evaluator-ready trace, rejecting unsafe/incomplete input."""
    forbidden = _forbidden_path(manifest)
    if forbidden:
        message = "qualification input contains forbidden key: "
        raise ValueError(message + forbidden)

    profile_id = _required_string(profile, "profile_id", "profile")
    if manifest.get("profile_id") != profile_id:
        mismatch = "manifest.profile_id does not match the frozen profile"
        raise ValueError(mismatch)
    provider = manifest.get("provider_configuration")
    if not isinstance(provider, Mapping):
        raise ValueError("manifest.provider_configuration is required")
    provider_spec = profile.get("provider_configuration", {})
    required_provider = provider_spec.get("required", [])
    for key in required_provider:
        _required_string(provider, str(key), "manifest.provider_configuration")

    compiled_runs = []
    raw_runs = manifest.get("runs")
    if not isinstance(raw_runs, list):
        raise ValueError("manifest.runs must be a list")
    for index, source in enumerate(raw_runs):
        if not isinstance(source, Mapping):
            raise ValueError(f"manifest.runs[{index}] must be a mapping")
        export = source.get("run_export")
        if not isinstance(export, Mapping):
            raise ValueError(f"manifest.runs[{index}].run_export is required")
        metrics = export.get("metrics")
        if not isinstance(metrics, Mapping):
            label = f"manifest.runs[{index}].run_export.metrics"
            raise ValueError(f"{label} is required")
        assertions = source.get("assertions")
        if not isinstance(assertions, Mapping):
            raise ValueError(f"manifest.runs[{index}].assertions is required")
        observed = _model_id(export)
        configured = str(provider.get("model_id") or "")
        if observed != configured:
            raise ValueError(
                f"manifest.runs[{index}] model {observed!r} does not match "
                f"configured model {configured!r}"
            )
        compiled_runs.append(
            {
                "run_id": _required_string(
                    export, "run_id", f"manifest.runs[{index}].run_export"
                ),
                "scenario_id": _required_string(
                    source, "scenario_id", f"manifest.runs[{index}]"
                ),
                "attempt": source.get("attempt"),
                "outcome": str(export.get("status") or "unknown"),
                "intervention_count": source.get("intervention_count"),
                "latency_ms": metrics.get("latency_ms"),
                "input_tokens": metrics.get("input_tokens"),
                "output_tokens": metrics.get("output_tokens"),
                "tool_retries": metrics.get("tool_retries"),
                "assertions": dict(assertions),
                "redacted_trace_ref": _required_string(
                    export,
                    "redacted_trace_ref",
                    f"manifest.runs[{index}].run_export",
                ),
            }
        )

    return {
        "profile_id": profile_id,
        "candidate_revision": _required_string(
            manifest, "candidate_revision", "manifest"
        ),
        "deployment_topology": _required_string(
            manifest, "deployment_topology", "manifest"
        ),
        "fixture_identity": _required_string(
            manifest,
            "fixture_identity",
            "manifest",
        ),
        "provider_configuration": dict(provider),
        "runs": compiled_runs,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Compile a local redacted manifest into evaluator input."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    args = parser.parse_args(argv)
    profile = _load_mapping(args.profile)
    manifest = _load_mapping(args.manifest)
    trace = compile_trace(profile, manifest)
    args.trace.parent.mkdir(parents=True, exist_ok=True)
    args.trace.write_text(json.dumps(trace, indent=2) + "\n", encoding="utf-8")
    result = {"trace": str(args.trace), "runs": len(trace["runs"])}
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
