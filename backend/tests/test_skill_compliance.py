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
    """Known tools."""
    return {t["name"] for t in build_tool_catalogue()}


@pytest.fixture(scope="module")
def tool_schemas():
    """Tool schemas."""
    return {t["name"]: t["input_schema"] for t in build_tool_catalogue()}


def test_core_skill_files_present():
    """Core skill files present."""
    paths = iter_core_skill_paths()
    names = {p.parent.name for p in paths}
    assert names == set(CORE_INTEGRAL_SKILL_NAMES)


def test_all_skills_compliance(known_tools, tool_schemas):
    """All skills compliance."""
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
    """No plan steps frontmatter."""
    for path in iter_bundle_skill_paths():
        text = path.read_text(encoding="utf-8")
        assert "plan-steps:" not in text, f"{path} still has plan-steps"


def test_all_skills_zero_warnings(known_tools):
    """Format compliance does not require vendor-specific body sections."""
    reports = audit_all_skills(known_tool_names=known_tools)
    failures = []
    for r in reports:
        if r.tier not in ("core", "bundle_public"):
            continue
        warns = [i for i in r.issues if i.severity == "warning"]
        if warns:
            failures.append(
                f"{r.skill_key} ({r.score}/7): " + "; ".join(f"{w.code}" for w in warns)
            )
    assert not failures, "Skills with warnings:\n" + "\n".join(failures)


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
    """Tool call examples reject contract drift."""
    issues = check_tool_call_examples(body, _EXAMPLE_SCHEMAS)
    assert [issue.code for issue in issues] == [code]


def test_tool_call_examples_accept_published_contract():
    """Tool call examples accept published contract."""
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


def test_standard_accepts_arbitrary_markdown_body(tmp_path):
    """Standard accepts arbitrary markdown body."""
    from app.services.skill_compliance import check_skill_body, check_skill_file

    folder = tmp_path / "portable-skill"
    folder.mkdir()
    source = folder / "SKILL.md"
    source.write_text(
        "---\nname: portable-skill\ndescription: Handles the requested workflow.\nallowed-tools: first_tool second_tool\nmetadata:\n  author: Integral\n---\nPlain workflow instructions.\n"
    )
    assert check_skill_file(source, tier="core").ok
    assert check_skill_body("Plain workflow instructions.", tier="core") == []


@pytest.mark.parametrize(
    "field",
    [
        "spec: jv",
        "extends: action:integral/base",
        "requires-actions: [EmbeddedIntegralAction]",
        "tags: [example]",
        "allowed-tools: [first_tool]",
    ],
)
def test_standard_rejects_vendor_fields_and_list_tools(tmp_path, field):
    """Standard rejects vendor fields and list tools."""
    from app.services.skill_compliance import check_skill_file

    folder = tmp_path / "portable-skill"
    folder.mkdir()
    source = folder / "SKILL.md"
    source.write_text(
        f"---\nname: portable-skill\ndescription: Handles the requested workflow.\n{field}\n---\nInstructions.\n"
    )
    assert not check_skill_file(source, tier="core").ok


def test_all_shipped_skill_packages_follow_the_standard():
    """All shipped skill packages follow the standard."""
    from pathlib import Path

    from app.services.skill_compliance import check_skill_file

    root = Path(__file__).resolve().parents[2]
    paths = sorted(
        path
        for tree in (root / "agent", root / "examples", root / "backend/app/packages")
        for path in tree.rglob("SKILL.md")
    )
    assert len(paths) >= 22
    failures = []
    for path in paths:
        report = check_skill_file(path, tier="core")
        if not report.ok:
            failures.append(f"{path}: {[issue.code for issue in report.issues]}")
    assert not failures, "\n".join(failures)
