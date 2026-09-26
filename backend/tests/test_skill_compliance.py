"""Compliance tests for Integral core and bundle SKILL.md files."""

from __future__ import annotations

import pytest

from app.agentive.tooling.catalogue import build_tool_catalogue
from app.services.skill_compliance import (
    CORE_INTEGRAL_SKILL_NAMES,
    audit_all_skills,
    check_tool_call_examples,
    iter_bundle_skill_paths,
    iter_core_skill_paths,
)


@pytest.fixture(scope="module")
def known_tools():
    return {t["name"] for t in build_tool_catalogue()}


@pytest.fixture(scope="module")
def tool_schemas():
    return {t["name"]: t["input_schema"] for t in build_tool_catalogue()}


def test_core_skill_files_present():
    paths = iter_core_skill_paths()
    names = {p.parent.name for p in paths}
    assert names == set(CORE_INTEGRAL_SKILL_NAMES)


def test_all_skills_compliance(known_tools, tool_schemas):
    reports = audit_all_skills(known_tool_names=known_tools, tool_schemas=tool_schemas)
    assert len(reports) == len(iter_core_skill_paths()) + len(iter_bundle_skill_paths())
    failures = []
    for r in reports:
        errors = [i for i in r.issues if i.severity == "error"]
        if errors:
            failures.append(
                f"{r.skill_key} ({r.tier}): "
                + "; ".join(f"{e.code}: {e.message}" for e in errors)
            )
    assert not failures, "Skill compliance failures:\n" + "\n".join(failures)


def test_no_plan_steps_frontmatter():
    for path in iter_bundle_skill_paths():
        text = path.read_text(encoding="utf-8")
        assert "plan-steps:" not in text, f"{path} still has plan-steps"


def test_all_skills_zero_warnings(known_tools):
    """Public skills must meet full 7/7 bar with no warnings."""
    reports = audit_all_skills(known_tool_names=known_tools)
    failures = []
    for r in reports:
        if r.tier not in ("core", "bundle_public"):
            continue
        warns = [i for i in r.issues if i.severity == "warning"]
        if warns or r.score < 7:
            failures.append(
                f"{r.skill_key} ({r.score}/7): " + "; ".join(f"{w.code}" for w in warns)
            )
    assert not failures, "Skills with warnings or <7 sections:\n" + "\n".join(failures)


_EXAMPLE_SCHEMAS = {
    "integral_count_entries": {
        "type": "object",
        "properties": {
            "group_by": {"type": "string", "enum": ["track", "status"]},
            "status": {"type": "string"},
        },
    },
    "integral_query_spec": {
        "type": "object",
        "properties": {
            "spec": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"resource": {}, "select": {}, "sort": {}},
            }
        },
    },
}


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ('`integral_count_things(group_by="track")`', "unknown_tool_call"),
        ('`integral_count_entries(group="track")`', "unknown_tool_argument"),
        ('`integral_count_entries(group_by="owner")`', "invalid_tool_argument_value"),
        (
            '`integral_query_spec(spec={resource: "entry", order: []})`',
            "unknown_tool_argument",
        ),
    ],
)
def test_tool_call_examples_reject_contract_drift(body, code):
    issues = check_tool_call_examples(body, _EXAMPLE_SCHEMAS)
    assert [issue.code for issue in issues] == [code]


def test_tool_call_examples_accept_published_contract():
    body = (
        '`integral_count_entries(group_by="track", status="open")` and '
        '`integral_query_spec(spec={resource: "entry", select: ["id"], '
        'sort: [{field: "title", direction: "asc"}]})` and the positional '
        "`integral_count_entries(group_by, status)`."
    )
    assert check_tool_call_examples(body, _EXAMPLE_SCHEMAS) == []


def test_bundle_manifests_synced():
    """operational-model.yaml skill entries must be full dicts synced from SKILL.md."""
    import subprocess
    import sys
    from pathlib import Path

    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "sync_bundle_skill_manifests.py"
    )
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        cwd=str(script.parents[1]),
    )
    assert result.returncode == 0, result.stdout + result.stderr
