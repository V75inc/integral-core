"""W0.3a deterministic qualification contract: scorer, split custody, evidence.

Runs in CI without model credentials. The scorer is pinned against
hand-authored expected reports, the committed split manifest must hold no
corpus content, and every committed package evidence record must satisfy the
plan §7 template.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

pytestmark = pytest.mark.smoke

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "qualification"
EVIDENCE = ROOT / "docs" / "product" / "evidence"


def _script(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evaluator = _script("evaluate_live_model_qualification")
contract = _script("validate_qualification_contract")


def _yaml(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _recorded_manifest() -> Dict[str, Any]:
    digest = "sha256:" + "a" * 64
    return {
        "profile_id": "synthetic-scorer-v1",
        "corpus": {
            "status": "recorded",
            "custodian": "Q held-out custodian",
            "locator": "restricted://qualification/synthetic",
            "version": "v1",
            "digest": digest,
        },
        "cases": [
            {"id": "synthetic_alpha", "split": "held_out", "digest": digest},
            {"id": "synthetic_beta", "split": "development", "digest": digest},
        ],
    }


def _evidence() -> Dict[str, Any]:
    return {
        "package_id": "W9.1",
        "candidate_sha": "b" * 40,
        "owner": "Q",
        "fixture": {
            "id": "synthetic_alpha",
            "digest": "sha256:" + "c" * 64,
            "split": "held_out",
        },
        "inputs": {"ref": "restricted://qualification/synthetic/alpha"},
        "revision": "design r3",
        "receipt": ["receipt-1"],
        "readback": "readback-1 equals expected aggregate",
        "assertions": {"command": "pytest tests/x.py", "passed": 4, "failed": 0},
        "limits": "synthetic",
        "live_qualification": "pending W0.3b baseline",
        "result": "pass",
    }


@pytest.mark.parametrize("outcome", ["pass", "fail"])
def test_scorer_matches_independent_expected_reports(
    outcome: str, tmp_path: Path
) -> None:
    report_path = tmp_path / "report.json"
    code = evaluator.main(
        [
            "--profile",
            str(FIXTURES / "profile.yaml"),
            "--trace",
            str(FIXTURES / f"trace-{outcome}.yaml"),
            "--report",
            str(report_path),
        ]
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    expected = json.loads(
        (FIXTURES / f"expected-report-{outcome}.json").read_text(encoding="utf-8")
    )

    assert code == (0 if outcome == "pass" else 1)
    assert report == expected
    assert "leaked-held-out-text" not in report_path.read_text(encoding="utf-8")


def test_committed_split_manifest_holds_ids_only_and_gates_live_runs() -> None:
    profile = _yaml(EVIDENCE / "wp-06-live-model-qualification.yaml")
    manifest = _yaml(EVIDENCE / "wp-06-split-manifest.yaml")

    assert contract.validate_split_manifest(manifest, profile) == []
    assert contract.validate_split_manifest(manifest, profile, True) == [
        "corpus is pending Q custody; live qualification refused"
    ]


def test_split_manifest_rejects_content_and_contradictions() -> None:
    profile = _yaml(FIXTURES / "profile.yaml")
    assert contract.validate_split_manifest(_recorded_manifest(), profile, True) == []

    leaked = _recorded_manifest()
    leaked["cases"][0]["expected_records"] = [{"value": 1}]
    moved = _recorded_manifest()
    moved["cases"][0]["split"] = "development"
    duplicated = _recorded_manifest()
    duplicated["cases"].append(dict(duplicated["cases"][0]))
    undigested = _recorded_manifest()
    undigested["cases"][1]["digest"] = None
    pending = _recorded_manifest()
    pending["corpus"]["status"] = "pending_q_custody"
    missing = _recorded_manifest()
    del missing["cases"][1]

    def failures(manifest: Dict[str, Any]) -> str:
        return "\n".join(contract.validate_split_manifest(manifest, profile))

    assert "expected_records carries corpus content" in failures(leaked)
    assert "contradicts profile held_out" in failures(moved)
    assert "more than one split entry" in failures(duplicated)
    assert "synthetic_beta.digest must be sha256" in failures(undigested)
    assert "corpus.locator must stay null until recorded" in failures(pending)
    assert "synthetic_beta is missing a split" in failures(missing)


def test_committed_package_evidence_records_satisfy_the_template() -> None:
    records = sorted((EVIDENCE / "packages").glob("*.yaml"))
    assert records, "Wave 0 evidence records are missing"
    for path in records:
        record = _yaml(path)
        assert record["package_id"] == path.stem
        assert contract.validate_evidence_record(record) == [], path.name


def test_evidence_record_rejects_incomplete_or_unsafe_records() -> None:
    assert contract.validate_evidence_record(_evidence()) == []

    cases = {
        "readback is required": lambda r: r.pop("readback"),
        "receipt.not_applicable needs a reason": lambda r: r.update(
            receipt={"not_applicable": ""}
        ),
        "candidate_sha must be a full": lambda r: r.update(candidate_sha="fc0d011"),
        "held_out inputs must be an access-controlled": lambda r: r.update(
            inputs="Track the rental fleet ..."
        ),
        "result pass requires": lambda r: r["assertions"].update(failed=1),
        "prompt carries corpus content": lambda r: r["inputs"].update(prompt="x"),
        "result must be one of": lambda r: r.update(result="green"),
    }
    for message, mutate in cases.items():
        record = copy.deepcopy(_evidence())
        mutate(record)
        assert message in "\n".join(contract.validate_evidence_record(record)), message


def test_cli_validates_committed_artifacts(capsys: pytest.CaptureFixture) -> None:
    args = [
        "--evidence",
        *map(str, sorted((EVIDENCE / "packages").glob("*.yaml"))),
        "--split-manifest",
        str(EVIDENCE / "wp-06-split-manifest.yaml"),
        "--profile",
        str(EVIDENCE / "wp-06-live-model-qualification.yaml"),
    ]
    assert contract.main(args) == 0
    assert contract.main([*args, "--require-recorded"]) == 1
    capsys.readouterr()
