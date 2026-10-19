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
    elif change == "true_fix":
        item["fixAvailable"] = True
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


def test_actionable_cli_prints_exact_non_secret_review_inputs(
    tmp_path, monkeypatch, capsys
):
    import json

    data, lock = snapshot()
    lock["packages"]["node_modules/braces"]["dev"] = False
    lock["packages"]["node_modules/braces"][
        "resolved"
    ] = "https://token:secret@example.invalid/archive"
    audit = tmp_path / "audit.json"
    lockfile = tmp_path / "package-lock.json"
    audit.write_text(json.dumps(data))
    lockfile.write_text(json.dumps(lock))
    monkeypatch.setattr(policy.sys, "argv", ["policy", str(audit), "1", str(lockfile)])
    assert policy.main() == 0
    output = capsys.readouterr().out
    line = next(
        line for line in output.splitlines() if line.startswith("REVIEW_CONTEXT ")
    )
    context = json.loads(line.removeprefix("REVIEW_CONTEXT "))
    assert context["review_expires"] == "2026-11-15"
    assert context["policy_date"]
    observed = context["packages"]["braces"]
    assert observed["fixAvailable"] == data["vulnerabilities"]["braces"]["fixAvailable"]
    assert observed["via"] == data["vulnerabilities"]["braces"]["via"]
    assert observed["nodes"] == [
        {"path": "node_modules/braces", "version": "3.0.3", "dev": False}
    ]
    assert "token:secret" not in output


def test_observed_ci_no_automatic_fix_keeps_exact_reviewed_graph_exception():
    import json

    fixture = json.loads(
        (Path(__file__).parent / "fixtures/frontend-audit-ci-20261015.json").read_text()
    )
    lines = policy.analyze(fixture["audit"], fixture["lock"], today=TODAY)
    assert sum(line.startswith("ACCEPTED high") for line in lines) == 5
    assert not any(line.startswith("ACTIONABLE") for line in lines)


def test_no_automatic_fix_never_accepts_an_unknown_advisory():
    data, lock = snapshot()
    data["vulnerabilities"]["braces"]["fixAvailable"] = False
    data["vulnerabilities"]["braces"]["via"][0][
        "url"
    ] = "https://github.com/advisories/GHSA-unknown"
    assert policy.analyze(data, lock, today=TODAY)[0].startswith("ACTIONABLE")


@pytest.mark.parametrize(
    "change", ["non_dev", "new_version", "new_advisory", "expired", "direct_patch"]
)
def test_ci_no_automatic_fix_variant_retains_all_other_review_limits(change):
    import json

    fixture = json.loads(
        (Path(__file__).parent / "fixtures/frontend-audit-ci-20261015.json").read_text()
    )
    today = TODAY
    if change == "non_dev":
        fixture["lock"]["packages"]["node_modules/braces"]["dev"] = False
    elif change == "new_version":
        fixture["lock"]["packages"]["node_modules/braces"]["version"] = "3.0.4"
    elif change == "new_advisory":
        fixture["audit"]["vulnerabilities"]["braces"]["via"][0][
            "url"
        ] = "https://github.com/advisories/GHSA-unknown"
    elif change == "expired":
        today = policy.REVIEW_EXPIRES
    else:
        fixture["audit"]["vulnerabilities"]["braces"]["fixAvailable"] = {
            "name": "braces",
            "version": "3.0.4",
            "isSemVerMajor": False,
        }
    assert any(
        line.startswith("ACTIONABLE")
        for line in policy.analyze(fixture["audit"], fixture["lock"], today=today)
    )


@pytest.mark.parametrize(
    "change",
    [
        "unchanged",
        "runtime_node",
        "version",
        "critical",
        "new_advisory",
        "direct_patch",
        "expired",
    ],
)
def test_reviewed_animation_peer_chain_keeps_all_boundaries(change):
    import json

    fixture = json.loads(
        (Path(__file__).parent / "fixtures/frontend-audit-ci-20261015.json").read_text()
    )
    name = "tailwindcss-animate"
    item = {
        "severity": "high",
        "fixAvailable": False,
        "nodes": ["node_modules/" + name],
        "via": ["tailwindcss"],
    }
    row = {"version": "1.0.7", "dev": True}
    fixture["audit"]["vulnerabilities"][name] = item
    fixture["lock"]["packages"]["node_modules/" + name] = row
    today = date(2026, 10, 18)
    if change == "runtime_node":
        row["dev"] = False
    elif change == "version":
        row["version"] = "1.0.8"
    elif change == "critical":
        item["severity"] = "critical"
    elif change == "new_advisory":
        item["via"] = [
            {
                "name": name,
                "severity": "high",
                "range": "*",
                "url": "https://github.com/advisories/GHSA-unknown",
            }
        ]
    elif change == "direct_patch":
        item["fixAvailable"] = {
            "name": name,
            "version": "1.0.8",
            "isSemVerMajor": False,
        }
    elif change == "expired":
        today = policy.REVIEW_EXPIRES
    line = next(
        line
        for line in policy.analyze(fixture["audit"], fixture["lock"], today=today)
        if name in line
    )
    assert line.startswith("ACCEPTED") is (change == "unchanged")
