"""Operational app acceptance: real tools, approval, schemas, links and routines."""

import pytest

from app.agentive.batch_validation import (
    materialize_scaffold_defaults,
    materialize_scaffold_view_bindings,
    scaffold_missing,
    validate_batch_references,
)
from app.agentive.staging import StagingError


def _op(kind, **payload):
    return {"kind": kind, "payload": payload}


def test_completeness_is_per_track():
    ops = [
        _op("create_app", name="Operations"),
        _op(
            "create_track", title="A", app_id="{{app.id}}", entry_types=[{"name": "A"}]
        ),
        _op("create_track", title="B", app_id="{{app.id}}"),
        _op(
            "save_view",
            track_id="{{track.id:A}}",
            view_type="table",
            config={"columns": [{"field": "custom_fields.code"}]},
        ),
        _op(
            "save_view",
            track_id="{{track.id:A}}",
            view_type="table",
            config={"columns": [{"field": "custom_fields.code"}]},
        ),
        _op("create_entry", track_id="{{track.id:A}}"),
        _op("create_entry", track_id="{{track.id:A}}"),
    ]
    missing = scaffold_missing(ops)
    assert len(missing) == 3
    assert all("'B'" in m for m in missing)
    assert len(scaffold_missing(ops, allow_empty=True)) == 2


def test_empty_table_does_not_count_as_a_materialized_scaffold_view():
    ops = [
        _op("create_app", name="Operations"),
        _op(
            "create_track",
            title="Assets",
            app_id="{{app.id}}",
            entry_types=[{"name": "Asset", "fields": [{"key": "serial"}]}],
        ),
        _op(
            "save_view",
            track_id="{{track.id:Assets}}",
            view_type="table",
            config={},
        ),
        _op("create_entry", track_id="{{track.id:Assets}}"),
    ]

    missing = scaffold_missing(ops)
    assert any("schema-bound view" in item for item in missing)
    assert any("config.columns" in item for item in missing)


def test_scaffold_materializes_unbound_table_from_inline_schema():
    """An agent's generic table becomes a usable schema-bound view at commit."""
    ops = [
        _op("create_app", name="Operations"),
        _op(
            "create_track",
            title="Assets",
            app_id="{{app.id}}",
            entry_types=[
                {
                    "name": "Asset",
                    "fields": [{"key": "serial", "name": "Serial", "type": "text"}],
                }
            ],
        ),
        _op(
            "save_view",
            track_id="{{track.id:Assets}}",
            view_type="table",
            name="All assets",
            config={"columns": [{"field": "title", "label": "Asset"}]},
        ),
        _op("create_entry", track_id="{{track.id:Assets}}"),
    ]

    assert materialize_scaffold_view_bindings(ops) == 1
    columns = ops[2]["payload"]["config"]["columns"]
    assert columns[1] == {"field": "custom_fields.serial", "label": "Serial"}
    assert scaffold_missing(ops) == []


def test_scaffold_defaults_complete_an_interrupted_schema_bearing_track():
    """A stopped tool sequence still has a usable baseline at commit time."""
    ops = [
        _op("create_app", name="Vehicle Maintenance"),
        _op(
            "create_app_track",
            title="Vehicles",
            app_id="{{app.id}}",
            entry_types=[
                {
                    "name": "Vehicle",
                    "fields": [
                        {"key": "registration_number", "type": "text"},
                        {"key": "next_service_date", "type": "date"},
                    ],
                }
            ],
        ),
    ]

    assert materialize_scaffold_defaults(ops) == 3
    assert [op["kind"] for op in ops[2:]] == [
        "save_view",
        "save_view",
        "create_entry",
    ]
    assert materialize_scaffold_view_bindings(ops) == 1
    assert scaffold_missing(ops) == []
    assert ops[2]["payload"]["config"]["columns"] == [
        {"field": "title", "label": "Name"},
        {"field": "custom_fields.registration_number", "label": "registration_number"},
        {"field": "custom_fields.next_service_date", "label": "next_service_date"},
    ]
    assert ops[3]["payload"]["config"] == {
        "calendar_mapping": {"dateField": "custom_fields.next_service_date"}
    }
    assert ops[4]["payload"]["title"] == "Example Vehicles"
    assert ops[4]["payload"]["entry_type"] == "Vehicle"
    assert ops[4]["payload"]["fields"]["registration_number"] == (
        "Example registration_number"
    )
    assert ops[4]["payload"]["fields"]["next_service_date"].count("-") == 2


def test_scaffold_defaults_enriches_a_blank_model_seed_record():
    """A title-only demo must visibly exercise the declared schema."""
    ops = [
        _op("create_app", name="Inspections"),
        _op(
            "create_app_track",
            title="Inspections",
            app_id="{{app.id}}",
            entry_types=[
                {
                    "name": "Inspection",
                    "fields": [
                        {"key": "location", "type": "text"},
                        {"key": "inspection_date", "type": "date"},
                        {"key": "outcome", "type": "select", "enum": ["pass", "fail"]},
                    ],
                }
            ],
        ),
        _op("create_entry", track_id="{{track.id:Inspections}}", title="Demo"),
    ]

    materialize_scaffold_defaults(ops)

    seed = ops[2]["payload"]
    assert seed["entry_type"] == "Inspection"
    assert seed["fields"]["location"] == "Example location"
    assert seed["fields"]["inspection_date"].count("-") == 2
    assert seed["fields"]["outcome"] == "pass"


def test_scaffold_preserves_valid_schema_bound_view():
    ops = [
        _op(
            "create_track",
            title="Assets",
            entry_types=[
                {"name": "Asset", "fields": [{"key": "serial", "type": "text"}]}
            ],
        ),
        _op(
            "save_view",
            track_id="{{track.id:Assets}}",
            view_type="table",
            name="Serials",
            config={"columns": [{"field": "custom_fields.serial", "label": "Serial"}]},
        ),
    ]
    assert materialize_scaffold_view_bindings(ops) == 0


@pytest.mark.parametrize(
    ("view_type", "expected"),
    [
        (
            "kanban",
            {
                "group_by": "custom_fields.status",
                "kanban_columns": [
                    {"key": "open", "label": "Open"},
                    {"key": "closed", "label": "Closed"},
                ],
            },
        ),
        (
            "calendar",
            {"calendar_mapping": {"dateField": "custom_fields.due_date"}},
        ),
    ],
)
def test_scaffold_materializes_other_schema_bound_view_types(view_type, expected):
    ops = [
        _op(
            "create_track",
            title="Work",
            entry_types=[
                {
                    "name": "Task",
                    "fields": [
                        {"key": "status", "type": "select", "enum": ["open", "closed"]},
                        {"key": "due_date", "type": "date"},
                    ],
                }
            ],
        ),
        _op(
            "save_view",
            track_id="{{track.id:Work}}",
            view_type=view_type,
            name=view_type.title(),
            config={},
        ),
    ]
    assert materialize_scaffold_view_bindings(ops) == 1
    assert ops[1]["payload"]["config"] == expected


@pytest.mark.parametrize("target", ["{{track.id:Later}}", "{{track.id:Typo}}"])
def test_forward_and_unknown_references_fail_before_apply(target):
    with pytest.raises(StagingError, match="unresolved references"):
        validate_batch_references(
            [
                _op("create_entry", track_id=target),
                _op("create_track", title="Later"),
            ]
        )


def test_inline_schema_rejected_before_track_creation():
    from app.agentive.tooling.bindings import _stage_create_track

    with pytest.raises(
        Exception,
        match="(?i)(unknown|unsupported|invalid).*(type|field)|type.*(unknown|unsupported|invalid)",
    ):
        _stage_create_track(
            {
                "name": "Invalid",
                "entry_types": [
                    {
                        "name": "Record",
                        "fields": [{"key": "x", "type": "imaginary_type"}],
                    }
                ],
            }
        )


@pytest.mark.asyncio
async def test_car_rental_build_through_tools_and_approval(
    authenticated_client, test_user
):
    from app.agentive import staging
    from app.agentive.tooling.dispatch import dispatch_tool
    from app.models.edges import CONTAINS, REFERENCES
    from app.models.nodes import ChatMessage, ChatThread, Entry, Track
    from app.services.agent_scope import current_scope_workspace_id
    from app.services.app_graph import get_track_attached_content_profile

    staging._reset_for_tests()
    response = await authenticated_client.post(
        "/api/workspaces", json={"name": "Rental acceptance"}
    )
    assert response.status_code == 200, response.text
    ws = response.json()["workspace"]["id"]
    sid = "rental-acceptance"
    # Staging executors resolve AuthUser by id; Integral User nodes expose that
    # as ``user_id``. Dispatch principal_id accepts either.
    auth_uid = getattr(test_user, "user_id", None) or test_user.id
    thread = await ChatThread.create(
        user_id=auth_uid, workspace_id=ws, provider_session_id=sid
    )
    msg = await ChatMessage.create(
        role="user", thread_id=thread.id, content="Build rental app"
    )
    await thread.connect(msg, edge=CONTAINS)

    async def call(tool, **args):
        result = await dispatch_tool(
            tool, args, principal_id=auth_uid, scope=ws, session_id=sid
        )
        assert not result.is_error, (tool, result)
        return result.data

    design = await call(
        "integral_propose_design",
        summary="Car rental",
        proposal=(
            "Cars with registration, availability, service and document dates; Renters with contact details; "
            "Rentals with car and renter links and status. Tables for all tracks, daily due-date reminders in chat."
        ),
    )
    assert design.get("_kind") == "design_outline", design
    msg = await ChatMessage.create(
        role="user", thread_id=thread.id, content="Looks good, please build it"
    )
    await thread.connect(msg, edge=CONTAINS)
    from app.services.chat_threads import stamp_design_approved

    thread = await ChatThread.get(thread.id)
    assert thread is not None
    assert await stamp_design_approved(
        thread=thread, utterance="Looks good, please build it"
    )
    await call("integral_begin_batch", label="Rental acceptance")
    await call(
        "integral_create_app",
        name="Car Rental Acceptance",
        description="Fleet, renters, rentals and due dates.",
    )

    def field(key, typ="text", **extra):
        return {"key": key, "name": key.replace("_", " ").title(), "type": typ, **extra}

    def lookup(key, track, et):
        return field(
            key,
            "relation",
            relation={
                "target": "entry",
                "target_track_types": [track],
                "target_entry_types": [et],
                "allow_cross_track": True,
                "many": False,
            },
        )

    shapes = {
        "Cars": (
            "Car",
            [
                field("registration"),
                field(
                    "availability",
                    "select",
                    enum=["available", "rented", "maintenance"],
                ),
                field("next_service_date", "date"),
                field("registration_expiry", "date"),
            ],
        ),
        "Renters": ("Renter", [field("contact")]),
        "Rentals": (
            "Rental",
            [
                lookup("car", "cars", "car"),
                lookup("renter", "renters", "renter"),
                field("start_date", "date"),
                field("due_date", "date"),
                field("status", "select", enum=["active", "returned"]),
            ],
        ),
    }
    for title, (etype, fields) in shapes.items():
        await call(
            "integral_create_app_track",
            app_id="{{app.id}}",
            name=title,
            description=f"Manage {title.lower()}.",
            entry_types=[{"name": etype, "fields": fields}],
        )
        await call(
            "integral_save_view",
            track_id="{{track.id:" + title + "}}",
            name="All " + title,
            view_type="table",
            config={
                "columns": [
                    {"field": "title", "label": title[:-1]},
                    {
                        "field": "custom_fields." + fields[0]["key"],
                        "label": fields[0]["name"],
                    },
                ]
            },
        )
    await call(
        "integral_create_entry",
        track_id="{{track.id:Cars}}",
        title="Demo Car A",
        entry_type="Car",
        fields={
            "registration": "DEMO-A",
            "availability": "rented",
            "next_service_date": "2026-10-01",
        },
    )
    await call(
        "integral_create_entry",
        track_id="{{track.id:Renters}}",
        title="Demo Renter A",
        entry_type="Renter",
        fields={"contact": "Demonstration only"},
    )
    await call(
        "integral_create_entry",
        track_id="{{track.id:Rentals}}",
        title="Demo Rental A",
        entry_type="Rental",
        fields={
            "car": "{{entry.id:Demo Car A}}",
            "renter": "{{entry.id:Demo Renter A}}",
            "status": "active",
        },
    )
    await call(
        "integral_author_skill",
        app_id="{{app.id}}",
        name="Return a car",
        description="Records a rental return and updates fleet availability.",
        tools_required=["integral_query_entries", "integral_update_entry"],
        body_override="""## When to use
Return a rental.
## When NOT to use
New reservations.
## Grounding
Read rental and car with integral_query_entries.
## Procedure
Stage returned status and available car with integral_update_entry.
## Staging discipline
Wait for approval, then verify.
## Forbidden patterns
Never invent ids or claim a concurrent booking lock.
## Example
Return Demo Rental A.
""",
    )
    await call(
        "integral_schedule_task",
        cron="0 9 * * *",
        timezone="America/Guyana",
        instruction=(
            "Query Cars track {{track.id:Cars}} for next_service_date and registration_expiry. "
            "Report overdue or due within 14 days, ignoring blank dates and demo records. Link matching cars in this chat."
        ),
    )
    committed = await call("integral_commit_batch", summary="Complete rental app")
    assert committed.get("_kind") == "batch_applied", committed
    assert committed.get("applied") is True, committed
    exec_result = committed.get("execute_result") or {}
    assert exec_result.get("filed") is not False and not exec_result.get(
        "error"
    ), committed
    # Chat affirmation applies the greenfield batch directly. Reset the test
    # staging state before post-build verification tools run.
    staging._reset_for_tests()
    scope_token = current_scope_workspace_id.set(ws)
    apps = await call("integral_list_apps")
    assert any(
        a.get("name") == "Car Rental Acceptance" for a in (apps.get("apps") or [])
    ), {"apps": apps, "committed": committed}
    app = next(a for a in apps["apps"] if a["name"] == "Car Rental Acceptance")
    tracks = await call("integral_list_tracks", app_id=app["id"])
    assert {t["title"] for t in tracks["tracks"]} == set(shapes)
    for t in tracks["tracks"]:
        node = await Track.get(t["id"])
        cp = await get_track_attached_content_profile(node)
        types = await cp.nodes(edge=[CONTAINS], node=["EntryType"])
        expected = shapes[t["title"]][1]
        assert {f["key"] for f in expected} <= {
            f["key"] for et in types for f in et.form_schema["fields"]
        }
        views = await call("integral_list_views", track_id=t["id"])
        assert "All " + t["title"] in str(views)
    rental_track = next(t for t in tracks["tracks"] if t["title"] == "Rentals")
    rentals = await Entry.find({"context.track_id": rental_track["id"]})
    rental = next(e for e in rentals if e.title == "Demo Rental A")
    linked = await rental.nodes(edge=[REFERENCES], node=["Entry"])
    assert {e.title for e in linked} == {"Demo Car A", "Demo Renter A"}
    routines = await call("integral_list_routines")
    assert "America/Guyana" in str(routines)
    assert "{{track.id" not in str(routines)
    assert "active" in str(routines)
    current_scope_workspace_id.reset(scope_token)
    staging._reset_for_tests()
