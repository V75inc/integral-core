#!/usr/bin/env python3
"""Validate W0.3a qualification artifacts: package evidence and split manifests.

Core keeps only identifiers, splits, and digests for the qualification corpus.
Domain prompts and expected records live in an external, access-controlled
artifact under Q custody, so any of those keys appearing in a Core artifact is
a contract failure, not a formatting issue. The checks are deterministic and
need no model credentials.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import yaml

FORBIDDEN_CONTENT_KEYS = frozenset(
    {
        "prompt",
        "prompts",
        "completion",
        "messages",
        "expected",
        "expected_records",
        "expected_outcome",
        "api_key",
        "authorization",
        "tool_observations",
    }
)
EVIDENCE_FIELDS = (
    "package_id",
    "candidate_sha",
    "owner",
    "fixture",
    "inputs",
    "revision",
    "receipt",
    "readback",
    "assertions",
    "limits",
    "live_qualification",
    "result",
)
RESULTS = frozenset({"pass", "fail", "blocked", "incomplete"})
SPLITS = frozenset({"development", "held_out"})
CORPUS_STATUSES = frozenset({"pending_q_custody", "recorded"})
PACKAGE_ID = re.compile(r"^W\d+\.\d+[a-z]?$")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _forbidden_paths(value: Any, path: str = "$") -> List[str]:
    found: List[str] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            nested_path = f"{path}.{key}"
            if str(key).lower() in FORBIDDEN_CONTENT_KEYS:
                found.append(f"{nested_path} carries corpus content")
            found.extend(_forbidden_paths(nested, nested_path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found.extend(_forbidden_paths(nested, f"{path}[{index}]"))
    return found


def _present(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return value not in (None, {}, [])


def validate_evidence_record(record: Mapping[str, Any]) -> List[str]:
    """Return failures for one per-package evidence record (plan §7 template)."""
    failures = _forbidden_paths(record)
    for name in EVIDENCE_FIELDS:
        value = record.get(name)
        if not _present(value):
            failures.append(f"{name} is required")
        elif isinstance(value, Mapping) and "not_applicable" in value:
            if not _present(value["not_applicable"]):
                failures.append(f"{name}.not_applicable needs a reason")
    if failures:
        return failures

    if not PACKAGE_ID.match(str(record["package_id"])):
        failures.append("package_id must look like W<wave>.<n>")
    if not FULL_SHA.match(str(record["candidate_sha"])):
        failures.append("candidate_sha must be a full 40-character commit SHA")
    if record["result"] not in RESULTS:
        failures.append(f"result must be one of {sorted(RESULTS)}")

    fixture = record["fixture"]
    if not isinstance(fixture, Mapping):
        failures.append("fixture must be a mapping")
    elif "not_applicable" not in fixture:
        if not _present(fixture.get("id")):
            failures.append("fixture.id is required")
        if not DIGEST.match(str(fixture.get("digest", ""))):
            failures.append("fixture.digest must be sha256:<64 hex>")
        if fixture.get("split") not in SPLITS:
            failures.append(f"fixture.split must be one of {sorted(SPLITS)}")
        inputs = record["inputs"]
        if fixture.get("split") == "held_out" and (
            not isinstance(inputs, Mapping) or set(inputs) != {"ref"}
        ):
            failures.append("held_out inputs must be an access-controlled {ref: ...}")

    assertions = record["assertions"]
    if not isinstance(assertions, Mapping):
        failures.append("assertions must be a mapping")
    else:
        if not _present(assertions.get("command")):
            failures.append("assertions.command is required")
        counts = {k: assertions.get(k) for k in ("passed", "failed")}
        if not all(isinstance(v, int) and v >= 0 for v in counts.values()):
            failures.append("assertions.passed and .failed must be counts")
        elif record["result"] == "pass" and (counts["failed"] or not counts["passed"]):
            failures.append("result pass requires passed > 0 and failed == 0")
    return failures


def validate_split_manifest(
    manifest: Mapping[str, Any],
    profile: Mapping[str, Any],
    require_recorded: bool = False,
) -> List[str]:
    """Return failures for a Core-side split manifest against its WP-06 profile.

    ``require_recorded`` is the W0.3b gate: a live run refuses a corpus that is
    still pending custody.
    """
    failures = _forbidden_paths(manifest)
    if manifest.get("profile_id") != profile.get("profile_id"):
        failures.append("profile_id does not match the frozen profile")

    corpus = manifest.get("corpus")
    if not isinstance(corpus, Mapping):
        return failures + ["corpus is required"]
    status = corpus.get("status")
    recorded = status == "recorded"
    if status not in CORPUS_STATUSES:
        failures.append(f"corpus.status must be one of {sorted(CORPUS_STATUSES)}")
    elif require_recorded and not recorded:
        failures.append("corpus is pending Q custody; live qualification refused")
    if not _present(corpus.get("custodian")):
        failures.append("corpus.custodian is required")
    for name in ("locator", "version", "digest"):
        if recorded and not _present(corpus.get(name)):
            failures.append(f"corpus.{name} is required once recorded")
        if not recorded and corpus.get(name) is not None:
            failures.append(f"corpus.{name} must stay null until recorded")
    if recorded and not DIGEST.match(str(corpus.get("digest", ""))):
        failures.append("corpus.digest must be sha256:<64 hex>")

    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        return failures + ["cases must be a non-empty list"]
    splits: Dict[str, str] = {}
    for index, case in enumerate(cases):
        label = f"cases[{index}]"
        if not isinstance(case, Mapping) or not _present(case.get("id")):
            failures.append(f"{label}.id is required")
            continue
        case_id = str(case["id"])
        if case_id in splits:
            failures.append(f"{case_id} appears in more than one split entry")
        if case.get("split") not in SPLITS:
            failures.append(f"{case_id}.split must be one of {sorted(SPLITS)}")
        splits[case_id] = str(case.get("split"))
        digest = case.get("digest")
        if recorded and not DIGEST.match(str(digest or "")):
            failures.append(f"{case_id}.digest must be sha256:<64 hex>")
        if not recorded and digest is not None:
            failures.append(f"{case_id}.digest must stay null until recorded")

    for scenario in profile.get("scenarios", []):
        scenario_id = scenario.get("id")
        wanted = scenario.get("prompt_variant")
        if scenario_id not in splits:
            failures.append(f"profile scenario {scenario_id} is missing a split")
        elif splits[scenario_id] != wanted:
            failures.append(
                f"{scenario_id} split {splits[scenario_id]} contradicts profile {wanted}"
            )
    return failures


def _load(path: Path) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a mapping")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    """Validate evidence records and/or a split manifest; exit 1 on failure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, nargs="*", default=[])
    parser.add_argument("--split-manifest", type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--require-recorded", action="store_true")
    args = parser.parse_args(argv)
    if args.split_manifest and not args.profile:
        parser.error("--split-manifest requires --profile")

    report: Dict[str, List[str]] = {}
    for path in args.evidence:
        report[str(path)] = validate_evidence_record(_load(path))
    if args.split_manifest:
        report[str(args.split_manifest)] = validate_split_manifest(
            _load(args.split_manifest), _load(args.profile), args.require_recorded
        )
    print(json.dumps(report, indent=2))
    return 1 if any(report.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
