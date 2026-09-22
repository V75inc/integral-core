"""Approved one-call scaffold plans preserve the existing batch authority."""

from types import SimpleNamespace

import pytest

from app.agentive.staging import _reset_for_tests, is_batch_open, open_batch
from app.agentive.tooling import scaffold_build
from app.agentive.tooling.dispatch import ToolResult


@pytest.fixture(autouse=True)
def _reset_batch():
    _reset_for_tests()
    yield
    _reset_for_tests()


@pytest.fixture
def approved(monkeypatch):
    thread = SimpleNamespace(
        user_id="user-1",
        design_proposed={
            "approved": True,
            "summary": "Bicycle repair app",
            "proposal": "Bicycle Repair Management with Customers and Repair Jobs tracks.",
        },
    )

    async def affirmed(_session_id):
        return True

    async def get_thread(_session_id):
        return thread

    monkeypatch.setattr(scaffold_build, "design_chat_affirmed_for_build", affirmed)
    monkeypatch.setattr(scaffold_build, "get_thread_by_session", get_thread)
    return thread


def _operations():
    return [
        {"tool": "integral_create_app", "args": {"name": "Bicycle Repair Management"}},
        {
            "tool": "integral_create_app_track",
            "args": {
                "name": "Customers",
                "app_id": "{{app.id}}",
                "entry_types": [
                    {"name": "Customer", "fields": [{"key": "name", "type": "text"}]}
                ],
            },
        },
    ]


def test_scaffold_plan_expands_wiki_parent_binding():
    operations = scaffold_build._expand_track(
        {
            "name": "Pages",
            "entry_types": [
                {
                    "name": "Page",
                    "fields": [{"key": "parent_page", "type": "relation"}],
                }
            ],
            "views": [{"name": "Wiki", "type": "wiki", "parent_field": "parent_page"}],
        }
    )
    assert operations[1][0] == "integral_save_view"
    assert operations[1][1]["view_type"] == "wiki"
    assert operations[1][1]["config"]["parent_field"] == "parent_page"


@pytest.mark.asyncio
async def test_applied_design_cannot_build_again(approved):
    approved.design_proposed["build_receipt"] = {"batch_token": "already-applied"}
    result = await scaffold_build.build_approved_design(
        {"operations": _operations()},
        principal_id="user-1",
        scope="workspace-1",
        session_id="thread-1",
        interaction_id=None,
    )
    assert result.error_code == "design_already_applied"
    assert not is_batch_open("user-1", "thread-1")


@pytest.mark.asyncio
async def test_partially_applied_design_cannot_create_duplicate_app(approved):
    approved.design_proposed["partial_build"] = {"batch_token": "partial-1"}
    result = await scaffold_build.build_approved_design(
        {"operations": _operations()},
        principal_id="user-1",
        scope="workspace-1",
        session_id="thread-1",
        interaction_id=None,
    )
    assert result.error_code == "partial_build_requires_repair"
    assert not is_batch_open("user-1", "thread-1")


@pytest.mark.asyncio
async def test_design_only_turn_refuses_library_write_before_staging():
    from app.agentive.tooling.dispatch import (
        clear_proposal_only_guard,
        dispatch_tool,
        set_proposal_only_guard,
    )

    set_proposal_only_guard("design-only-session")
    try:
        result = await dispatch_tool(
            "integral_author_model",
            {"name": "Premature library model"},
            principal_id="user-1",
            scope="workspace-1",
            session_id="design-only-session",
        )
        assert result.error_code == "design_proposal_only"
        assert not is_batch_open("user-1", "design-only-session")
    finally:
        clear_proposal_only_guard("design-only-session")


@pytest.mark.asyncio
async def test_requires_approved_design_before_staging(monkeypatch):
    async def not_affirmed(_session_id):
        return False

    monkeypatch.setattr(scaffold_build, "design_chat_affirmed_for_build", not_affirmed)
    result = await scaffold_build.build_approved_design(
        {"operations": _operations()},
        principal_id="user-1",
        scope="workspace-1",
        session_id="thread-1",
        interaction_id=None,
    )
    assert result.error_code == "design_approval_required"
    assert not is_batch_open("user-1", "thread-1")


@pytest.mark.asyncio
async def test_rejects_unapproved_names_and_scope_args(approved):
    wrong_name = _operations()
    wrong_name[1]["args"]["name"] = "Invoices"
    result = await scaffold_build.build_approved_design(
        {"operations": wrong_name},
        principal_id="user-1",
        scope="workspace-1",
        session_id="thread-1",
        interaction_id=None,
    )
    assert result.error_code == "plan_differs_from_design"

    scope_args = _operations()
    scope_args[0]["args"]["workspace_id"] = "other"
    result = await scaffold_build.build_approved_design(
        {"operations": scope_args},
        principal_id="user-1",
        scope="workspace-1",
        session_id="thread-1",
        interaction_id=None,
    )
    assert result.error_code == "invalid_scaffold_plan"
    assert not is_batch_open("user-1", "thread-1")


@pytest.mark.asyncio
async def test_detached_library_model_in_plan_gets_actionable_refusal(approved):
    operations = _operations()
    operations[1]["tool"] = "integral_author_model"
    result = await scaffold_build.build_approved_design(
        {"operations": operations},
        principal_id="user-1",
        scope="workspace-1",
        session_id="thread-1",
        interaction_id=None,
    )
    assert result.error_code == "invalid_scaffold_plan"
    assert "integral_author_model" in result.message
    assert "integral_create_app_track" in result.message
    assert not is_batch_open("user-1", "thread-1")


@pytest.mark.asyncio
async def test_rejects_missing_fields_promised_in_saved_design(approved):
    from app.agentive.tooling import scaffold_build

    approved.design_proposed["acceptance_assertions"] = [
        "Customers track with fields: Registration, Service Due Date (date), Notes"
    ]
    operations = _operations()
    operations[1]["args"]["fields"] = [
        {"key": "registration", "name": "Registration", "type": "text"}
    ]
    result = await scaffold_build.build_approved_design(
        {"operations": operations},
        principal_id="user-1",
        scope="workspace-1",
        session_id="thread-1",
        interaction_id=None,
    )
    assert result.error_code == "plan_differs_from_design"
    assert "service due date" in result.message
    assert "notes" in result.message
    assert not is_batch_open("user-1", "thread-1")


def test_markdown_proposal_fields_are_approval_requirements():
    from app.agentive.tooling.scaffold_build import _approved_field_requirements

    marker = {
        "proposal": "### 1. Cars Track\n- **Fields:**\n  - Registration Number (text)\n  - Next Service Date (date)\n- **Views:**\n  - Table (all cars)\n### 2. Customers Track\n- **Fields:**\n  - Email (text)"
    }
    assert _approved_field_requirements(marker) == {
        "cars": {"registration number", "next service date"},
        "customers": {"email"},
    }


def test_inline_views_heading_is_not_mistaken_for_approved_field():
    from app.agentive.tooling.scaffold_build import _approved_field_requirements

    marker = {
        "proposal": "### 1. Customers\n- **Fields:**\n  - Name (text)\n- **Views:** Table (all customers)\n### 2. Jobs\n- **Fields:**\n  - Status (select)"
    }
    assert _approved_field_requirements(marker) == {
        "customers": {"name"},
        "jobs": {"status"},
    }

    plain = {
        "proposal": "### 1. Vehicles\n- Fields:\n  - Registration Number (text)\n  - Next Service Date (date)\n- Views:\n  - Table (all vehicles)"
    }
    assert _approved_field_requirements(plain) == {
        "vehicles": {"registration number", "next service date"}
    }

    compressed_assertion = {
        "acceptance_assertions": [
            "Cars track with fields: last/next service, rental start/end"
        ],
        "proposal": "### 1. Cars Track\n- **Fields:**\n  - Last Service Date (date)\n  - Next Service Due (date)\n  - Rental Start Date (date)\n  - Rental End Date (date)",
    }
    assert _approved_field_requirements(compressed_assertion)["cars"] == {
        "last service date",
        "next service due",
        "rental start date",
        "rental end date",
    }


def test_labelled_seed_text_becomes_fields_and_named_relation_refs():
    from app.agentive.tooling.scaffold_build import _structured_seed

    tracks = {
        "{{track.id:Rentals}}": [
            {"key": "vehicle", "name": "Vehicle", "type": "relation"},
            {"key": "status", "name": "Status", "type": "select"},
        ]
    }
    entry = _structured_seed(
        {
            "track_id": "{{track.id:Rentals}}",
            "title": "Rental for Honda Civic",
            "text": "Vehicle: Honda Civic\nStatus: Active",
        },
        tracks,
        {"honda civic": "Honda Civic"},
    )
    assert entry["fields"] == {
        "vehicle": "{{entry.id:Honda Civic}}",
        "status": "Active",
    }
    assert "text" not in entry

    with pytest.raises(ValueError, match="does not match a declared field"):
        _structured_seed(
            {"track_id": "{{track.id:Rentals}}", "text": "Vehicle Honda Civic"},
            tracks,
            {"honda civic": "Honda Civic"},
        )

    with pytest.raises(ValueError, match="use a relation field"):
        _structured_seed(
            {"track_id": "{{track.id:Jobs}}", "text": "Assigned Technician: Alice"},
            {
                "{{track.id:Jobs}}": [
                    {
                        "key": "assigned_technician",
                        "name": "Assigned Technician",
                        "type": "member",
                    }
                ]
            },
            {"alice": "Alice"},
        )


def test_oversized_repeated_plan_coalesces_without_losing_seed_refinement():
    from app.agentive.tooling.scaffold_build import _coalesce_plan_operations

    view = {
        "tool": "integral_save_view",
        "args": {"track_id": "{{track.id:Cars}}", "name": "Cars Table"},
    }
    first = {
        "tool": "integral_create_entry",
        "args": {
            "track_id": "{{track.id:Cars}}",
            "title": "Toyota Camry",
            "fields": {"status": "Rented"},
        },
    }
    refined = {
        "tool": "integral_create_entry",
        "args": {
            "track_id": "{{track.id:Cars}}",
            "title": "Toyota Camry",
            "fields": {"status": "Rented", "current_renter": "{{entry.id:John Doe}}"},
        },
    }
    result = _coalesce_plan_operations([view] * 90 + [first, refined])
    assert result == [view, refined]

    conflict = {
        **refined,
        "args": {**refined["args"], "fields": {"status": "Available"}},
    }
    with pytest.raises(ValueError, match="Conflicting seed fields"):
        _coalesce_plan_operations([first, conflict])


@pytest.mark.asyncio
async def test_stages_and_commits_once_with_bound_identity(approved, monkeypatch):
    from app.agentive.tooling import dispatch

    calls = []

    async def stage(tool, args, **context):
        calls.append((tool, args, context))
        return ToolResult(data={"batched": True})

    async def commit(name, args, **context):
        calls.append((name, args, context))
        return ToolResult(
            data={
                "_kind": "batch_applied",
                "applied": True,
                "token": "batch-1",
                "execute_result": {"completed": 2, "total": 2, "results": []},
            }
        )

    monkeypatch.setattr(dispatch, "dispatch_tool", stage)
    monkeypatch.setattr(dispatch, "_dispatch_batch_control", commit)
    result = await scaffold_build.build_approved_design(
        {"operations": _operations()},
        principal_id="user-1",
        scope="workspace-1",
        session_id="thread-1",
        interaction_id="interaction-1",
    )

    assert [call[0] for call in calls] == [
        "integral_create_app",
        "integral_create_app_track",
        "integral_save_view",
        "integral_commit_batch",
    ]
    assert all(call[2]["principal_id"] == "user-1" for call in calls)
    assert all(call[2]["scope"] == "workspace-1" for call in calls)
    assert result.data == {
        "_kind": "batch_applied",
        "applied": True,
        "batch_token": "batch-1",
        "completed": 2,
        "total": 2,
        "next": "Read back each Track schema, saved views, dashboard data and seeded entries against the approved assertions before saying verified.",
    }


@pytest.mark.asyncio
async def test_empty_preparatory_batch_is_accepted(approved, monkeypatch):
    from app.agentive.tooling import dispatch

    await open_batch(user_id="user-1", session_id="thread-1")

    async def stage(_tool, _args, **_context):
        return ToolResult(data={"batched": True})

    async def commit(_name, _args, **_context):
        return ToolResult(
            data={
                "_kind": "batch_applied",
                "applied": True,
                "execute_result": {"completed": 2, "total": 2},
            }
        )

    monkeypatch.setattr(dispatch, "dispatch_tool", stage)
    monkeypatch.setattr(dispatch, "_dispatch_batch_control", commit)
    result = await scaffold_build.build_approved_design(
        {"operations": _operations()},
        principal_id="user-1",
        scope="workspace-1",
        session_id="thread-1",
        interaction_id=None,
    )
    assert result.data["applied"] is True


def test_design_shorthand_compiles_to_bound_schema_views_and_dashboard_filters():
    from app.agentive.tooling.scaffold_build import (
        _dashboard_from_date_views,
        _expand_track,
        _normalize_dashboard,
        _normalize_view,
    )
    from app.services.relative_date_filters import normalize_relative_date

    expanded = _expand_track(
        {
            "name": "Deliverables",
            "app_id": "{{app.id}}",
            "fields": [
                {
                    "key": "status",
                    "name": "Status",
                    "type": "select",
                    "enum": ["Open", "Done"],
                },
                {"key": "due_date", "name": "Due date", "type": "date"},
            ],
            "views": [
                {"name": "All Deliverables", "type": "table"},
                {"name": "Due dates", "type": "calendar", "date_field": "due_date"},
            ],
        }
    )
    assert expanded[0][1]["entry_types"][0]["fields"][1]["key"] == "due_date"
    assert expanded[1][1]["config"]["columns"] == [
        "custom_fields.status",
        "custom_fields.due_date",
    ]
    assert expanded[2][1]["config"]["calendar_mapping"] == {
        "dateField": "custom_fields.due_date"
    }

    dashboard = _normalize_dashboard(
        normalize_relative_date(
            {
                "widgets": [
                    {
                        "name": "Overdue",
                        "type": "list",
                        "track_id": "{{track.id:Deliverables}}",
                        "filters": {"due_date": {"$lt": "{{now}}"}},
                    }
                ]
            }
        )
    )
    widget = dashboard["widgets"][0]
    assert widget["type"] == "recent_entries"
    assert widget["data_source"]["filters"] == [
        {
            "field": "custom_fields.due_date",
            "op": "lt",
            "value": {"relative_date_days": 0},
        }
    ]

    excluded = _normalize_dashboard(
        {
            "widgets": [
                {
                    "type": "recent_entries",
                    "title": "Open jobs",
                    "data_source": {
                        "filters": [
                            {
                                "field": "custom_fields.status",
                                "op": "not_in",
                                "value": ["Completed", "Cancelled"],
                            }
                        ]
                    },
                }
            ]
        }
    )
    assert excluded["widgets"][0]["data_source"]["filters"] == [
        {"field": "custom_fields.status", "op": "neq", "value": "Completed"},
        {"field": "custom_fields.status", "op": "neq", "value": "Cancelled"},
    ]

    view = _normalize_view(
        {
            "track_id": "{{track.id:Deliverables}}",
            "name": "Overdue Deliverables",
            "config": {
                "filter": [
                    {
                        "field": "custom_fields.due_date",
                        "op": "lt",
                        "value": {"relative_date_days": 0},
                    }
                ]
            },
        }
    )
    assert view["config"]["filters"][0]["operator"] == "lt"
    generated = _dashboard_from_date_views(
        [("integral_save_view", view)], "Consulting Practice Management App"
    )
    assert generated["widgets"][0]["data_source"]["filters"][0]["op"] == "lt"

    symbolic = _normalize_view(
        {
            "config": {
                "filter": [
                    {
                        "field": "custom_fields.due_date",
                        "operator": ">=",
                        "value": "2026-09-22",
                    }
                ]
            }
        }
    )
    assert symbolic["config"]["filters"][0]["operator"] == "gte"
    generated_symbolic = _dashboard_from_date_views(
        [
            (
                "integral_save_view",
                {
                    **symbolic,
                    "name": "Upcoming Deliverables",
                    "track_id": "{{track.id:Deliverables}}",
                },
            )
        ],
        "Consulting Practice Management App",
    )
    from app.schemas.dashboards import DataSourceSpec

    parsed = DataSourceSpec.model_validate(
        generated_symbolic["widgets"][0]["data_source"]
    )
    assert parsed.filters[0].op == "gte"


@pytest.mark.asyncio
async def test_redundant_terminal_commit_is_absorbed(approved, monkeypatch):
    from app.agentive.tooling import dispatch

    seen = []

    async def stage(tool, args, **context):
        seen.append(tool)
        return ToolResult(data={"batched": True})

    async def commit(*args, **kwargs):
        return ToolResult(data={"_kind": "batch_applied", "applied": True})

    monkeypatch.setattr(dispatch, "dispatch_tool", stage)
    monkeypatch.setattr(dispatch, "_dispatch_batch_control", commit)
    result = await scaffold_build.build_approved_design(
        {"operations": _operations() + [{"tool": "integral_commit_batch", "args": {}}]},
        principal_id="user-1",
        scope="workspace-1",
        session_id="thread-1",
        interaction_id=None,
    )
    assert result.data["applied"] is True
    assert "integral_commit_batch" not in seen


@pytest.mark.asyncio
async def test_ordering_metadata_does_not_block_real_reference_validation(
    approved, monkeypatch
):
    from app.agentive.tooling import dispatch

    async def stage(*args, **kwargs):
        return ToolResult(data={"batched": True})

    async def commit(*args, **kwargs):
        return ToolResult(data={"_kind": "batch_applied", "applied": True})

    monkeypatch.setattr(dispatch, "dispatch_tool", stage)
    monkeypatch.setattr(dispatch, "_dispatch_batch_control", commit)
    operations = _operations()
    operations[1]["depends_on"] = ["{{app.id}}"]
    result = await scaffold_build.build_approved_design(
        {"operations": operations},
        principal_id="user-1",
        scope="workspace-1",
        session_id="thread-1",
        interaction_id=None,
    )
    assert result.data["applied"] is True


@pytest.mark.asyncio
async def test_partial_batch_is_error_with_recovery_receipt(approved, monkeypatch):
    from app.agentive.tooling import dispatch

    async def stage(*args, **kwargs):
        return ToolResult(data={"batched": True})

    async def commit(*args, **kwargs):
        return ToolResult(
            data={
                "_kind": "batch_apply_failed",
                "applied": False,
                "token": "partial-1",
                "execute_result": {
                    "error_code": "batch_partial_failure",
                    "message": "step 3 failed",
                    "completed": 2,
                    "total": 3,
                    "results": [
                        {"kind": "create_dashboard", "result": {"error": True}}
                    ],
                },
            }
        )

    monkeypatch.setattr(dispatch, "dispatch_tool", stage)
    monkeypatch.setattr(dispatch, "_dispatch_batch_control", commit)
    result = await scaffold_build.build_approved_design(
        {"operations": _operations()},
        principal_id="user-1",
        scope="workspace-1",
        session_id="thread-1",
        interaction_id=None,
    )
    assert result.is_error
    assert result.error_code == "batch_partial_failure"
    assert result.data["batch_token"] == "partial-1"
    assert result.data["failed_step"]["kind"] == "create_dashboard"


@pytest.mark.asyncio
async def test_stage_failure_discards_unapplied_batch(approved, monkeypatch):
    from app.agentive.tooling import dispatch

    async def stage(tool, _args, **_context):
        if tool == "integral_create_app_track":
            return ToolResult(
                is_error=True, error_code="invalid_fields", message="Bad fields"
            )
        return ToolResult(data={"batched": True})

    monkeypatch.setattr(dispatch, "dispatch_tool", stage)
    result = await scaffold_build.build_approved_design(
        {"operations": _operations()},
        principal_id="user-1",
        scope="workspace-1",
        session_id="thread-1",
        interaction_id=None,
    )
    assert result.error_code == "scaffold_plan_stage_failed"
    assert not is_batch_open("user-1", "thread-1")
