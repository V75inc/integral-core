"""Only the reviewed build graph receives a temporary audit exception."""

import copy
import importlib.util
from datetime import date
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
path = Path(__file__).resolve().parents[2] / ".ci/frontend_dependency_policy.py"
spec = importlib.util.spec_from_file_location("frontend_dependency_policy", path)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
TODAY = date(2026, 10, 15)


def snapshot():
    item = {
        "severity": "high",
        "fixAvailable": {
            "name": "tailwindcss",
            "version": "4.3.3",
            "isSemVerMajor": True,
        },
        "nodes": ["node_modules/braces"],
        "via": [
            {
                "name": "braces",
                "url": "https://github.com/advisories/GHSA-vfj7-8cjw-p6xm",
                "severity": "high",
                "range": "<=3.0.3",
            }
        ],
    }
    return {"vulnerabilities": {"braces": item}}, {
        "packages": {"node_modules/braces": {"version": "3.0.3", "dev": True}}
    }


def test_exact_reviewed_build_graph_is_accepted_until_expiry():
    data, lock = snapshot()
    before = copy.deepcopy((data, lock))
    assert policy.analyze(data, lock, today=TODAY)[0].startswith("ACCEPTED high braces")
    assert (data, lock) == before


@pytest.mark.parametrize(
    "change",
    [
        "unknown_id",
        "unknown_package",
        "direct_patch",
        "true_fix",
        "false_fix",
        "new_major",
        "new_minor",
        "malformed_version",
        "changed_version",
        "non_dev",
        "wrong_node",
        "mixed_nodes",
        "higher_severity",
        "root_severity",
        "range",
        "source_name",
        "empty_via",
    ],
)
def test_exception_does_not_expand_beyond_reviewed_conditions(change):
    data, lock = snapshot()
    item = data["vulnerabilities"]["braces"]
    via = item["via"][0]
    row = lock["packages"]["node_modules/braces"]
    if change == "unknown_id":
        via["url"] = "https://github.com/advisories/GHSA-unreviewed"
    elif change == "unknown_package":
        data["vulnerabilities"] = {"unreviewed": {**item, "fixAvailable": False}}
    elif change == "direct_patch":
        item["fixAvailable"] = {
            "name": "braces",
            "version": "3.0.4",
            "isSemVerMajor": False,
        }
    elif change in {"true_fix", "false_fix"}:
        item["fixAvailable"] = change == "true_fix"
    elif change in {"new_major", "new_minor", "malformed_version"}:
        item["fixAvailable"]["version"] = {
            "new_major": "5.0.0",
            "new_minor": "3.4.20",
            "malformed_version": "4.invalid",
        }[change]
    elif change == "changed_version":
        row["version"] = "3.0.2"
    elif change == "non_dev":
        row["dev"] = False
    elif change == "wrong_node":
        item["nodes"] = ["node_modules/not-braces"]
        lock["packages"][item["nodes"][0]] = row
    elif change == "mixed_nodes":
        item["nodes"].append("node_modules/other/node_modules/braces")
        lock["packages"][item["nodes"][-1]] = {"version": "3.0.2", "dev": True}
    elif change == "higher_severity":
        item["severity"] = "critical"
    elif change == "root_severity":
        via["severity"] = "critical"
    elif change == "range":
        via["range"] = "<4"
    elif change == "source_name":
        via["name"] = "different"
    elif change == "empty_via":
        item["via"] = []
    assert any(
        line.startswith("ACTIONABLE")
        for line in policy.analyze(data, lock, today=TODAY)
    )


@pytest.mark.parametrize(
    "today,accepted",
    [
        (date(2026, 11, 14), True),
        (date(2026, 11, 15), False),
        (date(2027, 1, 1), False),
    ],
)
def test_expiry_is_a_real_gate(today, accepted):
    data, lock = snapshot()
    assert policy.analyze(data, lock, today=today)[0].startswith("ACCEPTED") is accepted


@pytest.mark.parametrize("shape", ["valid", "missing", "cycle", "unknown"])
def test_transitive_parent_needs_every_reviewed_child(shape):
    data, lock = snapshot()
    data["vulnerabilities"]["tailwindcss"] = {
        "severity": "high",
        "fixAvailable": data["vulnerabilities"]["braces"]["fixAvailable"],
        "nodes": ["node_modules/tailwindcss"],
        "via": ["braces"],
    }
    lock["packages"]["node_modules/tailwindcss"] = {"version": "3.4.19", "dev": True}
    if shape == "missing":
        del data["vulnerabilities"]["braces"]
    elif shape == "cycle":
        data["vulnerabilities"]["braces"]["via"] = ["tailwindcss"]
    elif shape == "unknown":
        data["vulnerabilities"]["braces"]["via"][0][
            "url"
        ] = "https://github.com/advisories/GHSA-unreviewed"
    if shape in {"missing", "cycle"}:
        with pytest.raises(ValueError):
            policy.analyze(data, lock, today=TODAY)
    else:
        lines = policy.analyze(data, lock, today=TODAY)
        assert all(line.startswith("ACCEPTED") for line in lines) is (shape == "valid")


def test_moderate_inventory_is_visible_and_not_misrepresented_as_clean():
    data, lock = snapshot()
    data["vulnerabilities"]["braces"]["severity"] = "moderate"
    lines = policy.analyze(data, lock, today=TODAY)
    assert lines == ["NOTICE moderate braces (outside blocking severity policy)"]


def test_reviewed_major_migration_can_receive_patch_updates():
    data, lock = snapshot()
    data["vulnerabilities"]["braces"]["fixAvailable"]["version"] = "4.4.0"
    assert policy.analyze(data, lock, today=TODAY)[0].startswith("ACCEPTED")


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"vulnerabilities": []},
        {"vulnerabilities": {"x": None}},
        {
            "vulnerabilities": {
                "x": {"severity": "unknown", "via": [], "fixAvailable": False}
            }
        },
        {
            "vulnerabilities": {
                "x": {"severity": "high", "via": "invalid", "fixAvailable": False}
            }
        },
        {"vulnerabilities": {"x": {"severity": "high", "via": []}}},
    ],
)
def test_invalid_audit_record_is_not_silently_skipped(document):
    with pytest.raises(ValueError):
        policy.analyze(document, {}, today=TODAY)
