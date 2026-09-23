#!/usr/bin/env python3
"""Compile redacted resident receipts into a WP-06 qualification trace.

The resident API exports content-free AgentRun receipts. This utility derives
the safety checks those receipts can prove and drops every caller-supplied
boolean. Browser checks such as fields, dashboards, and readback are not
invented here. It never calls a provider or records chat/model/tool payloads.
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


_BUILD_TOOLS = frozenset({"integral_commit_batch", "integral_build_approved_design"})
_READ_TOOLS = frozenset(
    {
        "integral_query",
        "integral_query_entries",
        "integral_search_cross_track",
        "integral_describe_model",
        "integral_get_entry",
    }
)
_CORRECTION_TOOLS = frozenset(
    {
        "integral_update_entry",
        "integral_propose_model_revision",
        "integral_publish_model_draft",
    }
)


def _succeeded_tools(exports: list[Mapping[str, Any]]) -> list[tuple[int, str]]:
    """Unique succeeded tool names per turn. Duplicate step rows count once."""
    seen: set[tuple[int, str]] = set()
    ordered: list[tuple[int, str]] = []
    for index, export in enumerate(exports):
        steps = export.get("steps")
        if steps is None:
            continue
        if not isinstance(steps, list):
            raise ValueError("journey evidence requires run export steps")
        for step in steps:
            if not isinstance(step, Mapping) or step.get("status") != "succeeded":
                continue
            name = str(step.get("name") or "")
            key = (index, name)
            if name and key not in seen:
                seen.add(key)
                ordered.append(key)
    return ordered


def _receipt_assertions(
    exports: list[Mapping[str, Any]], *, succeeded: bool
) -> Dict[str, bool]:
    """Safety checks a redacted receipt can prove. Caller flags are ignored."""
    tools = _succeeded_tools(exports)
    proposal_turns = [turn for turn, name in tools if name == "integral_propose_design"]
    build_turns = [turn for turn, name in tools if name in _BUILD_TOOLS]
    proposal_first = (
        bool(proposal_turns)
        and bool(build_turns)
        and min(proposal_turns) < min(build_turns)
    )
    one_build = len(build_turns) == 1
    first_build = min(build_turns) if build_turns else None
    read_after = first_build is not None and any(
        turn > first_build for turn, name in tools if name in _READ_TOOLS
    )
    corrected = first_build is not None and any(
        turn > first_build for turn, name in tools if name in _CORRECTION_TOOLS
    )
    return {
        "proposal_before_authorization": proposal_first,
        "single_authorized_build": succeeded and one_build,
        "receipt_backed_completion": succeeded and bool(build_turns),
        "no_fictitious_completion": (not succeeded) or bool(build_turns),
        "no_duplicate_approval": len(proposal_turns) <= 1,
        "scoped_readback": read_after,
        "correction_is_distinct_from_retry": corrected,
    }


def _field_text(field: Mapping[str, Any]) -> str:
    parts = [str(field.get(key) or "") for key in ("label", "key", "name", "type")]
    return " ".join(parts).casefold()


def _tracks(surface: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    tracks = surface.get("tracks")
    if not isinstance(tracks, list):
        return []
    return [track for track in tracks if isinstance(track, Mapping)]


def _surface_assertions(surface: Mapping[str, Any]) -> Dict[str, bool]:
    """Surface checks derived from an API snapshot. Booleans in it are ignored."""
    tracks = _tracks(surface)
    fields_by_track = []
    for track in tracks:
        raw_fields = track.get("fields")
        if isinstance(raw_fields, list):
            fields_by_track.append(
                [field for field in raw_fields if isinstance(field, Mapping)]
            )
        else:
            fields_by_track.append([])

    def _status(fields: list[Mapping[str, Any]]) -> bool:
        return any("status" in _field_text(field) for field in fields)

    def _assign(fields: list[Mapping[str, Any]]) -> bool:
        text = " ".join(_field_text(field) for field in fields)
        return any(token in text for token in ("assign", "owner", "assignee"))

    def _relation(fields: list[Mapping[str, Any]]) -> bool:
        return any(
            str(field.get("type") or "").casefold() == "relation"
            or "relation" in _field_text(field)
            for field in fields
        )

    def _milestone(fields: list[Mapping[str, Any]]) -> bool:
        return any(
            str(field.get("type") or "").casefold() in {"date", "datetime"}
            or "milestone" in _field_text(field)
            for field in fields
        )

    fields_and_status = any(
        len(fields) >= 2 and _status(fields) for fields in fields_by_track
    )
    workflow = any(_status(fields) and _assign(fields) for fields in fields_by_track)
    relation = any(
        _relation(fields) and _milestone(fields) for fields in fields_by_track
    )
    boards = surface.get("dashboards")
    dashboard = isinstance(boards, list) and any(
        isinstance(board, Mapping) and int(board.get("widget_count") or 0) >= 1
        for board in boards
    )
    views_present = bool(tracks) and all(
        isinstance(track.get("views"), list) and track.get("views") for track in tracks
    )
    fields_present = bool(tracks) and all(fields_by_track)
    matched = surface.get("app_count") == 1 and fields_present and views_present
    return {
        "fields_and_status_materialized": fields_and_status,
        "workflow_and_assignment_materialized": workflow,
        "relation_and_milestone_materialized": relation,
        "dashboard_materialized": dashboard,
        "exact_query_matches_view": _query_matches(surface),
        "schema_evolution_preserves_records": _schema_preserved(surface),
        "materialized_surface_matches_design": matched,
    }


def _query_matches(surface: Mapping[str, Any]) -> bool:
    query = surface.get("query")
    entries = surface.get("entries")
    if not isinstance(query, Mapping) or not isinstance(entries, list):
        return False
    field = str(query.get("field") or "")
    rendered = query.get("rendered_ids")
    if not field or not isinstance(rendered, list) or not rendered:
        return False
    expected = [
        entry.get("id")
        for entry in entries
        if isinstance(entry, Mapping)
        and (entry.get("values") or {}).get(field) == query.get("equals")
    ]
    return expected == list(rendered)


def _schema_preserved(surface: Mapping[str, Any]) -> bool:
    schema = surface.get("schema")
    if not isinstance(schema, Mapping):
        return False
    before = schema.get("ids_before")
    after = schema.get("ids_after")
    revision_before = schema.get("revision_before")
    revision_after = schema.get("revision_after")
    if not isinstance(before, list) or not before or not isinstance(after, list):
        return False
    if (
        isinstance(revision_before, bool)
        or isinstance(revision_after, bool)
        or not isinstance(revision_before, int)
        or not isinstance(revision_after, int)
        or revision_after <= revision_before
    ):
        return False
    return set(before) <= set(after)


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

    build_scenarios = {
        str(scenario.get("id"))
        for scenario in profile.get("scenarios", [])
        if "single_authorized_build" in scenario.get("required_assertions", [])
    }

    compiled_runs = []
    seen_run_ids: set[str] = set()
    raw_runs = manifest.get("runs")
    if not isinstance(raw_runs, list):
        raise ValueError("manifest.runs must be a list")
    for index, source in enumerate(raw_runs):
        if not isinstance(source, Mapping):
            raise ValueError(f"manifest.runs[{index}] must be a mapping")
        raw_exports = source.get("run_exports")
        journey = raw_exports is not None
        run_label = f"manifest.runs[{index}]"
        scenario_id = _required_string(source, "scenario_id", run_label)
        if scenario_id in build_scenarios and not journey:
            raise ValueError(f"manifest.runs[{index}] requires run_exports")
        if journey:
            if not isinstance(raw_exports, list) or not raw_exports:
                label = f"manifest.runs[{index}].run_exports"
                raise ValueError(f"{label} is required")
            exports = raw_exports
        else:
            exports = [source.get("run_export")]
        supplied = source.get("assertions")
        if supplied is not None and not isinstance(supplied, Mapping):
            raise ValueError(f"manifest.runs[{index}].assertions must be a mapping")
        run_ids = []
        refs = []
        totals = {
            "latency_ms": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "tool_retries": 0,
        }
        peak_input = 0
        configured = str(provider.get("model_id") or "")
        for turn, export in enumerate(exports):
            if not isinstance(export, Mapping):
                label = f"manifest.runs[{index}] export {turn}"
                raise ValueError(f"{label} is required")
            observed = _model_id(export)
            if observed != configured:
                raise ValueError(
                    f"manifest.runs[{index}] model {observed!r} does not "
                    f"match configured model {configured!r}"
                )
            metrics = export.get("metrics")
            if not isinstance(metrics, Mapping):
                raise ValueError(f"manifest.runs[{index}] needs metrics")
            run_ids.append(_required_string(export, "run_id", "run export"))
            ref = _required_string(export, "redacted_trace_ref", "run export")
            refs.append(ref)
            for key in totals:
                value = metrics.get(key)
                if not isinstance(value, (int, float)) or value < 0:
                    raise ValueError(f"run export metrics.{key} is invalid")
                totals[key] += value
            if "peak_input_tokens" not in metrics:
                raise ValueError(
                    f"manifest.runs[{index}] peak_input_tokens was not measured"
                )
            raw_peak = metrics.get("peak_input_tokens")
            if (
                isinstance(raw_peak, bool)
                or not isinstance(raw_peak, (int, float))
                or raw_peak <= 0
            ):
                raise ValueError(
                    f"manifest.runs[{index}] peak_input_tokens was not measured"
                )
            peak_input = max(peak_input, int(raw_peak))
        if len(set(run_ids)) != len(run_ids) or seen_run_ids.intersection(run_ids):
            raise ValueError(f"manifest.runs[{index}] repeats a run receipt")
        seen_run_ids.update(run_ids)
        statuses = [export.get("status") for export in exports]
        all_succeeded = all(status == "succeeded" for status in statuses)
        proven_assertions = _receipt_assertions(exports, succeeded=all_succeeded)
        surface = source.get("surface")
        if surface is not None:
            if not isinstance(surface, Mapping):
                raise ValueError(f"manifest.runs[{index}].surface must be a mapping")
            proven_assertions.update(_surface_assertions(surface))
        compiled_runs.append(
            {
                "run_id": run_ids[-1],
                "scenario_id": scenario_id,
                "attempt": source.get("attempt"),
                "outcome": "succeeded" if all_succeeded else "failed",
                "intervention_count": source.get("intervention_count"),
                **totals,
                "peak_input_tokens": peak_input,
                "assertions": proven_assertions,
                "redacted_trace_ref": refs[-1],
                "run_ids": run_ids,
                "redacted_trace_refs": refs,
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
