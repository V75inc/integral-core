"""Exact candidate audit exception and input-boundary regressions."""

import copy
import importlib.util
import json
import tempfile
import unittest
from datetime import date
from itertools import product
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "candidate_policy", ROOT / ".ci/frontend_dependency_policy.py"
)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
LOCK = json.loads((ROOT / "frontend/package-lock.json").read_text())
REVIEW = json.loads((ROOT / ".ci/frontend_dependency_review.json").read_text())
# Use historical advisory shapes as test fixtures, but bind the copied
# approvals to this test's lockfile. The checked-in release review remains
# unchanged and the real audit must establish the current candidate's status.
for candidate in (REVIEW, REVIEW["alternate_report"]):
    candidate["lock_sha256"] = policy.canonical_digest(LOCK)
    candidate["approval"] = {
        "decision": "approved",
        "owner": "Test fixture only",
        "approved_on": candidate["reviewed_on"],
        "evidence": "Synthetic dependency-policy test; never a release approval",
    }
AUDIT = {"vulnerabilities": copy.deepcopy(REVIEW["graph"])}


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.audit, self.lock, self.review = copy.deepcopy((AUDIT, LOCK, REVIEW))
        # Test approvals exercise policy and never mutate the reviewed decision.
        self.review["approval"] = {
            "decision": "approved",
            "owner": "Test only",
            "approved_on": "2026-11-17",
            "evidence": "test fixture",
        }
        self.today = date(2026, 11, 17)
        self.digest = self.review["build_inputs_sha256"]

    def lines(self):
        return policy.analyze(
            self.audit,
            self.lock,
            today=self.today,
            review=self.review,
            build_digest=self.digest,
        )

    def blocked(self):
        self.assertTrue(any(x.startswith("ACTIONABLE") for x in self.lines()))

    def test_exact_candidate_only(self):
        before = copy.deepcopy((self.audit, self.lock, self.review))
        lines = self.lines()
        self.assertEqual(sum(x.startswith("ACCEPTED") for x in lines), 14)
        self.assertEqual(sum(x.startswith("NOTICE") for x in lines), 2)
        self.assertFalse(any(x.startswith("ACTIONABLE") for x in lines))
        self.assertEqual((self.audit, self.lock, self.review), before)

    def test_unapproved_proposal_remains_blocked(self):
        self.review["approval"] = None
        self.blocked()

    def test_approval_requires_complete_explicit_decision(self):
        for approval in (
            True,
            "pending",
            {"owner": ""},
            {
                "decision": "pending",
                "owner": "Test",
                "approved_on": "2026-11-17",
                "evidence": "test",
            },
            {
                "decision": "approved",
                "owner": " ",
                "approved_on": "2026-11-17",
                "evidence": "test",
            },
            {
                "decision": "approved",
                "owner": "Test",
                "approved_on": "2026-11-18",
                "evidence": "test",
            },
        ):
            with self.subTest(approval=approval):
                self.review["approval"] = approval
                self.blocked()

    def test_expiry_boundary(self):
        self.today = date(2026, 11, 24)
        self.blocked()

    def test_before_review(self):
        self.today = date(2026, 11, 16)
        self.blocked()

    def test_cannot_silently_extend_window(self):
        self.review["expires"] = "2026-12-01"
        self.blocked()

    def test_changed_build_inputs(self):
        self.digest = "unreviewed"
        self.blocked()

    def test_missing_build_evidence(self):
        self.digest = None
        self.blocked()

    def test_changed_version(self):
        self.lock["packages"]["node_modules/picomatch"]["version"] = "4.0.8"
        self.blocked()

    def test_changed_integrity(self):
        self.lock["packages"]["node_modules/picomatch"]["integrity"] = "sha512-changed"
        self.blocked()

    def test_non_dev_even_if_review_digest_was_changed(self):
        self.lock["packages"]["node_modules/picomatch"]["dev"] = False
        self.review["lock_sha256"] = policy.canonical_digest(self.lock)
        self.blocked()

    def test_new_nested_node(self):
        self.audit["vulnerabilities"]["picomatch"]["nodes"].append(
            "node_modules/new/node_modules/picomatch"
        )
        self.blocked()

    def test_new_advisory_on_known_package(self):
        root = self.audit["vulnerabilities"]["picomatch"]["via"][0]
        root["url"] = "https://github.com/advisories/GHSA-new"
        self.blocked()

    def test_unknown_high_cannot_be_added_to_review_snapshot(self):
        root = self.audit["vulnerabilities"]["picomatch"]["via"][0]
        root["url"] = "https://github.com/advisories/GHSA-new"
        self.review["graph"]["picomatch"]["via"] = copy.deepcopy([root])
        self.blocked()

    def test_unknown_unrelated_high_still_blocks(self):
        self.audit["vulnerabilities"]["unknown"] = {
            "severity": "high",
            "via": [],
            "nodes": [],
            "fixAvailable": False,
        }
        self.assertIn(
            "ACTIONABLE high unknown (outside reviewed conditions)", self.lines()
        )

    def test_critical_severity(self):
        self.audit["vulnerabilities"]["picomatch"]["severity"] = "critical"
        self.blocked()

    def test_changed_range(self):
        self.audit["vulnerabilities"]["picomatch"]["via"][0]["range"] = "<5"
        self.blocked()

    def test_changed_remediation_identity(self):
        self.audit["vulnerabilities"]["picomatch"]["fixAvailable"] = {
            "name": "picomatch",
            "version": "4.0.8",
            "isSemVerMajor": False,
        }
        self.blocked()

    def test_only_observed_transitive_remediation_aliases_are_equivalent(self):
        from itertools import product

        migration = {"name": "tailwindcss", "version": "4.3.5", "isSemVerMajor": True}
        names = ("anymatch", "chokidar", "fast-glob", "readdirp")
        for values in product((True, migration), repeat=len(names)):
            with self.subTest(values=values):
                for name, value in zip(names, values):
                    self.audit["vulnerabilities"][name]["fixAvailable"] = copy.deepcopy(
                        value
                    )
                self.assertFalse(any(x.startswith("ACTIONABLE") for x in self.lines()))

    def test_transitive_alias_does_not_accept_new_remediation(self):
        for value in (
            False,
            1,
            {"name": "tailwindcss", "version": "4.3.5", "isSemVerMajor": 1},
            {"name": "tailwindcss", "version": "4.3.5", "isSemVerMajor": False},
            {"name": "tailwindcss", "version": "4.3.6", "isSemVerMajor": True},
            {"name": "anymatch", "version": "4.0.0", "isSemVerMajor": True},
        ):
            with self.subTest(value=value):
                self.audit["vulnerabilities"]["anymatch"]["fixAvailable"] = value
                self.blocked()

    def test_true_fix_is_not_a_blanket_exception(self):
        self.audit["vulnerabilities"]["picomatch"]["fixAvailable"] = True
        self.blocked()

    def test_missing_transitive_record(self):
        del self.audit["vulnerabilities"]["tinyglobby"]
        self.blocked()

    def test_changed_transitive_cycle(self):
        self.audit["vulnerabilities"]["picomatch"]["via"] = ["vite"]
        self.blocked()

    def test_malformed_document(self):
        self.audit = {
            "vulnerabilities": {
                "x": {"severity": "high", "via": "invalid", "fixAvailable": False}
            }
        }
        with self.assertRaises(ValueError):
            self.lines()

    def test_moderate_to_high_requires_new_review(self):
        self.audit["vulnerabilities"]["@vitest/mocker"]["severity"] = "high"
        self.blocked()

    def test_build_fingerprint_changes_for_source_and_new_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for rel in (
                "frontend/src",
                "frontend/plugins",
                "frontend/public",
                "frontend/scripts",
                "sdk/react/src",
                "sdk/react/bin",
                ".ci",
                ".github/workflows",
            ):
                (root / rel).mkdir(parents=True, exist_ok=True)
            for rel in (
                ".ci/bundle_web_assets.sh",
                ".ci/reproducible_build_env.sh",
                ".github/workflows/ci.yml",
                ".github/workflows/publish-testpypi.yml",
                ".github/workflows/publish-pypi.yml",
            ):
                (root / rel).write_text("build\n")
            source = root / "frontend/src/main.ts"
            source.write_text("safe\n")
            first = policy.build_inputs_digest(root)
            source.write_text("changed\n")
            second = policy.build_inputs_digest(root)
            self.assertNotEqual(first, second)
            (root / "frontend/src/new.ts").write_text("new\n")
            self.assertNotEqual(second, policy.build_inputs_digest(root))
            for rel in (
                "frontend/scripts/verify-ui-runtime.mjs",
                "sdk/react/bin/app-ui-guides.mjs",
            ):
                executable = root / rel
                executable.write_text("before")
                before_script = policy.build_inputs_digest(root)
                executable.write_text("after")
                self.assertNotEqual(before_script, policy.build_inputs_digest(root))
            with tempfile.TemporaryDirectory() as linked_directory:
                (Path(linked_directory) / "external.ts").write_text("external input")
                (root / "frontend/src/linked").symlink_to(
                    linked_directory, target_is_directory=True
                )
                with self.assertRaises(ValueError):
                    policy.build_inputs_digest(root)
            (root / "frontend/src/linked").unlink()
            (root / "sdk").rename(root / "external-sdk")
            (root / "sdk").symlink_to(root / "external-sdk", target_is_directory=True)
            with self.assertRaises(ValueError):
                policy.build_inputs_digest(root)


@pytest.mark.parametrize(
    "change",
    [
        "none",
        "missing_approval",
        "hybrid",
        "alias",
        "numeric_flag",
        "named_transitive_fix",
        "unreviewed_boolean",
        "direct_fix",
        "path",
        "dev",
        "version",
        "new_high",
        "expiry",
        "build",
    ],
)
def test_alternate_report_is_separately_approved_and_exact(change):
    review, lock = copy.deepcopy((REVIEW, LOCK))
    alternate = review["alternate_report"]
    audit = {"vulnerabilities": copy.deepcopy(alternate["graph"])}
    rows = audit["vulnerabilities"]
    plugin = "node_modules/tailwindcss-animate"
    today = date.fromisoformat(alternate["reviewed_on"])
    digest = alternate["build_inputs_sha256"]
    if change == "missing_approval":
        alternate["approval"] = None
    elif change == "hybrid":
        rows["picomatch"]["fixAvailable"] = review["graph"]["picomatch"]["fixAvailable"]
    elif change == "alias":
        rows["chokidar"]["fixAvailable"] = review["graph"]["chokidar"]["fixAvailable"]
    elif change == "numeric_flag":
        rows["anymatch"]["fixAvailable"] = 1
    elif change == "named_transitive_fix":
        rows["fast-glob"]["fixAvailable"] = {
            "name": "fast-glob",
            "version": "3.3.4",
            "isSemVerMajor": False,
        }
    elif change == "unreviewed_boolean":
        rows["micromatch"]["fixAvailable"] = True
    elif change == "direct_fix":
        rows["picomatch"]["fixAvailable"] = {
            "name": "picomatch",
            "version": "4.0.8",
            "isSemVerMajor": False,
        }
    elif change == "path":
        rows["tailwindcss-animate"]["nodes"] = ["node_modules/unreviewed"]
    elif change == "dev":
        lock["packages"][plugin]["dev"] = False
    elif change == "version":
        lock["packages"][plugin]["version"] = "1.0.8"
    elif change == "new_high":
        rows["unreviewed"] = copy.deepcopy(rows["picomatch"])
    elif change == "expiry":
        today = date.fromisoformat(alternate["expires"])
    elif change == "build":
        digest = "unreviewed inputs"
    lines = policy.analyze(audit, lock, review=review, build_digest=digest, today=today)
    assert any(line.startswith("ACTIONABLE") for line in lines) == (change != "none")
    if change == "none":
        assert sum(line.startswith("ACCEPTED") for line in lines) == 15


@pytest.mark.parametrize("flags", product((False, True), repeat=4))
def test_alternate_report_accepts_only_reviewed_boolean_variations(flags):
    alternate = REVIEW["alternate_report"]
    rows = copy.deepcopy(alternate["graph"])
    for name, flag in zip(("anymatch", "chokidar", "fast-glob", "readdirp"), flags):
        rows[name]["fixAvailable"] = flag
    lines = policy.analyze(
        {"vulnerabilities": rows},
        LOCK,
        review=REVIEW,
        build_digest=alternate["build_inputs_sha256"],
        today=date.fromisoformat(alternate["reviewed_on"]),
    )
    assert sum(line.startswith("ACCEPTED") for line in lines) == 15
    assert not any(line.startswith("ACTIONABLE") for line in lines)


if __name__ == "__main__":
    unittest.main(verbosity=2)
