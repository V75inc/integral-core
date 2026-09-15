"""ACC-05 — Standard Task EntryType assertions.

Confirms the **projects** library package declares a `task` EntryType
with the documented shape (bucket / status / assignee / due_date /
estimate / priority) and Planner-style kanban views — ``tasks-board``
grouped by ``bucket`` (primary) and ``tasks-by-status`` grouped by
``status``.

The Task EntryType lives in the `project-details` track template so it
is auto-provisioned on every Project entry's anchored detail track.

Phase 31 (DR-31-01 §3): the `project-details` track template and its
parent Projects track moved from `crm-plus-pm-suite` to the standalone
**projects** bundle.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.content_profile_compile import compile_canonical_manifest
from app.services.content_profile_loader import load_library_profiles_with_issues


@pytest.fixture(scope="module")
def project_details_template():
    specs, _ = load_library_profiles_with_issues(profiles_root=Path("app/profiles"))
    spec = next((s for s in specs if s.slug == "projects"), None)
    assert spec is not None
    compiled = compile_canonical_manifest(manifest=spec.manifest, scope_hint="app")
    tt = compiled["app"]["track_templates"]
    return next(t for t in tt if t["key"] == "project-details")


def test_task_entry_type_declared(project_details_template):
    et_keys = {et["key"] for et in project_details_template["entry_types"]}
    assert "task" in et_keys, et_keys


def test_task_entry_type_carries_acc_05_fields(project_details_template):
    """ACC-05 — Task carries bucket/status/assignee/due_date/estimate/priority."""
    task_et = next(
        et for et in project_details_template["entry_types"] if et["key"] == "task"
    )
    fields_by_key = {f["key"]: f for f in task_et["fields"]}

    assert fields_by_key["bucket"]["type"] == "select"
    assert set(fields_by_key["bucket"]["enum"]) == {
        "backlog",
        "this_week",
        "in_progress",
        "done",
    }

    # status (select): four-step status flow.
    assert fields_by_key["status"]["type"] == "select"
    assert set(fields_by_key["status"]["enum"]) == {
        "todo",
        "in_progress",
        "in_review",
        "done",
    }

    # assignee (member): the ACC-08 member field type.
    assert fields_by_key["assignee"]["type"] == "member"

    # due_date (date).
    assert fields_by_key["due_date"]["type"] == "date"

    # start_date + checklist (Planner polish).
    assert fields_by_key["start_date"]["type"] == "date"
    assert fields_by_key["checklist"]["type"] == "json"

    # estimate (number).
    assert fields_by_key["estimate"]["type"] == "number"

    # priority (select): low/medium/high.
    assert fields_by_key["priority"]["type"] == "select"
    assert set(fields_by_key["priority"]["enum"]) == {"low", "medium", "high"}


def test_task_kanban_view_keys_on_bucket(project_details_template):
    """Planner polish — tasks-board is kanban grouped by bucket."""
    tasks_board = next(
        v for v in project_details_template["views"] if v["key"] == "tasks-board"
    )
    assert tasks_board["view_type"] == "kanban"
    assert tasks_board["group_by"] == "custom_fields.bucket"
    assert tasks_board.get("entry_type_keys") == ["task"]
    columns = {c["key"] for c in tasks_board["kanban_columns"]}
    assert columns == {"backlog", "this_week", "in_progress", "done"}


def test_task_kanban_columns_align_with_bucket_enum(project_details_template):
    """Planner polish — tasks-board columns mirror the bucket enum 1:1."""
    task_et = next(
        et for et in project_details_template["entry_types"] if et["key"] == "task"
    )
    bucket_enum = set(
        next(f for f in task_et["fields"] if f["key"] == "bucket")["enum"]
    )
    tasks_board = next(
        v for v in project_details_template["views"] if v["key"] == "tasks-board"
    )
    column_keys = {c["key"] for c in tasks_board["kanban_columns"]}
    assert bucket_enum == column_keys


def test_tasks_by_status_view(project_details_template):
    """Secondary kanban grouped by workflow status."""
    view = next(
        v for v in project_details_template["views"] if v["key"] == "tasks-by-status"
    )
    assert view["view_type"] == "kanban"
    assert view["group_by"] == "custom_fields.status"
    column_keys = {c["key"] for c in view["kanban_columns"]}
    assert column_keys == {"todo", "in_progress", "in_review", "done"}


def test_tasks_board_is_default_view(project_details_template):
    defaults = project_details_template.get("defaults") or {}
    assert defaults.get("default_view") == "tasks-board"
