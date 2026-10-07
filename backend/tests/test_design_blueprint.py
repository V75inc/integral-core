"""W1.3 typed design blueprint: schema, revisions, and structural plan fidelity."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any, Dict

import pytest

from app.agentive.staging import _reset_for_tests
from app.agentive.tooling import scaffold_build
from app.agentive.tooling.dispatch import ToolResult
from app.models.edges import CONTAINS
from app.models.nodes import ChatMessage, ChatThread
from app.schemas.design_blueprint import DesignBlueprint
from app.services import chat_threads
from app.services.design_blueprint import (
    blueprint_diff,
    plan_fidelity_errors,
    validate_blueprint,
)

pytestmark = pytest.mark.smoke

_PROPOSAL = (
    "**Bike Repair** app.\n\n- **Jobs** track — customer, status, due date\n"
    "- Views: Jobs board by status to decide what to work on next.\n"
)


def _blueprint() -> Dict[str, Any]:
    return {
        "app": {"id": "app", "name": "Bike Repair"},
        "goals": [{"id": "goal.turnaround", "text": "Repair bikes on time"}],
        "tracks": [
            {
                "id": "track.jobs",
                "name": "Jobs",
                "entry_types": [
                    {
                        "id": "type.job",
                        "name": "Job",
                        "fields": [
                            {
                                "id": "f.customer",
                                "key": "customer",
                                "name": "Customer",
                                "type": "text",
                            },
                            {
                                "id": "f.status",
                                "key": "status",
                                "name": "Status",
                                "type": "select",
                                "options": ["Queued", "Done"],
                            },
                            {
                                "id": "f.due",
                                "key": "due_date",
                                "name": "Due date",
                                "type": "date",
                            },
                        ],
                    }
                ],
            }
        ],
        "views": [
            {
                "id": "view.board",
                "track": "track.jobs",
                "name": "Jobs board",
                "type": "kanban",
                "decision": "What to work on next",
                "config": {"group_by": "status"},
            }
        ],
        "platform_defaults": [
            {"id": "default.feed", "kind": "view", "detail": "Every Track has a Feed."}
        ],
    }


def _canonical() -> Dict[str, Any]:
    blueprint, error = validate_blueprint(_blueprint())
    assert error is None, error
    return blueprint


def _plan() -> list:
    return [
        {"tool": "integral_create_app", "args": {"name": "Bike Repair"}},
        {
            "tool": "integral_create_app_track",
            "args": {
                "name": "Jobs",
                "app_id": "{{app.id}}",
                "entry_types": [
                    {
                        "name": "Job",
                        "fields": [
                            {"key": "customer", "name": "Customer", "type": "text"},
                            {
                                "key": "status",
                                "name": "Status",
                                "type": "select",
                                "enum": ["Queued", "Done"],
                            },
                            {"key": "due_date", "name": "Due date", "type": "date"},
                        ],
                    }
                ],
            },
        },
        {
            "tool": "integral_save_view",
            "args": {
                "track_id": "{{track.id:Jobs}}",
                "name": "Jobs board",
                "view_type": "kanban",
                "config": {"group_by": "custom_fields.status"},
            },
        },
    ]


def test_schema_rejects_dangling_references_and_ambiguous_shapes() -> None:
    """Reject malformed shapes and unresolved blueprint references."""
    cases = {
        "item ids must be unique": lambda b: b["views"][0].update(id="f.status"),
        "unknown track": lambda b: b["views"][0].update(track="track.nope"),
        "unknown field keys": lambda b: b.update(
            seeds=[
                {
                    "id": "seed.one",
                    "track": "track.jobs",
                    "title": "A",
                    "fields": {"state": "Done"},
                }
            ]
        ),
        "should match pattern": lambda b: b.update(
            seeds=[
                {
                    "id": "seed.one",
                    "track": "track.jobs",
                    "title": "A",
                    "fields": {"Status": "Done"},
                }
            ]
        ),
        "relation config iff type is relation": lambda b: b["tracks"][0]["entry_types"][
            0
        ]["fields"][0].update(type="relation"),
        "exactly one of cron or run_at": lambda b: b.update(
            routines=[{"id": "r.daily", "name": "Daily", "purpose": "Remind"}]
        ),
        "decision": lambda b: b["views"][0].pop("decision"),
    }
    for message, mutate in cases.items():
        raw = _blueprint()
        mutate(raw)
        blueprint, error = validate_blueprint(raw)
        assert blueprint is None and message in (error or ""), (message, error)


def test_observed_model_blueprint_derives_schema_ids_and_accepts_enum() -> None:
    """Shape gpt-4.1 sent in the W1.3 browser smoke: no field ids, ``enum``."""
    raw = {
        "schema_version": 1,
        "app": {"id": "bike_repair", "name": "Bike Repair"},
        "tracks": [
            {
                "id": "jobs",
                "name": "Jobs",
                "entry_types": [
                    {
                        "id": "job",
                        "name": "Job",
                        "fields": [
                            {"key": "customer", "name": "Customer", "type": "text"},
                            {
                                "key": "status",
                                "name": "Status",
                                "type": "select",
                                "enum": ["Queued", "Done"],
                            },
                        ],
                    }
                ],
            }
        ],
        "views": [
            {
                "id": "jobs_kanban",
                "track": "jobs",
                "name": "Jobs by Status",
                "type": "kanban",
                "decision": "Workflow",
            }
        ],
    }
    blueprint, error = validate_blueprint(raw)

    assert error is None, error
    fields = blueprint["tracks"][0]["entry_types"][0]["fields"]
    assert [f["id"] for f in fields] == ["jobs.customer", "jobs.status"]
    assert fields[1]["options"] == ["Queued", "Done"]
    assert blueprint["tracks"][0]["entry_types"][0]["id"] == "job"


def test_repeated_field_ids_are_namespaced_before_global_uniqueness_check() -> None:
    """Give duplicate natural field IDs stable, track-scoped identities."""
    raw = _blueprint()
    second_track = copy.deepcopy(raw["tracks"][0])
    second_track.update(id="track.checkouts", name="Checkouts")
    second_track["entry_types"][0].update(id="type.checkout", name="Checkout")
    raw["tracks"].append(second_track)

    blueprint, error = validate_blueprint(raw)

    assert error is None, error
    assert [
        track["entry_types"][0]["fields"][1]["id"] for track in blueprint["tracks"]
    ] == ["track.jobs.type.job.status", "track.checkouts.type.checkout.status"]


def test_model_json_schema_explains_global_ids_and_entry_relation_constraints() -> None:
    """Expose cross-item and conditional relation rules to the model."""
    schema = DesignBlueprint.model_json_schema()
    definitions = schema["$defs"]

    assert (
        "unique across every item in this whole blueprint"
        in definitions["BlueprintApp"]["properties"]["id"]["description"]
    )
    assert (
        "REQUIRED and non-empty"
        in definitions["BlueprintRelation"]["properties"]["target_entry_types"][
            "description"
        ]
    )


def test_amendment_is_an_item_id_diff() -> None:
    """Report only added, removed, and changed stable item IDs."""
    before = _canonical()
    raw = _blueprint()
    fields = raw["tracks"][0]["entry_types"][0]["fields"]
    fields[0]["name"] = "Customer name"
    del fields[2]
    raw["views"].append(
        {
            "id": "view.table",
            "track": "track.jobs",
            "name": "All jobs",
            "type": "table",
            "decision": "Audit",
        }
    )
    after, _ = validate_blueprint(raw)

    assert blueprint_diff(before, after) == {
        "added": ["view.table"],
        "removed": ["f.due"],
        "changed": ["f.customer"],
    }


async def _thread(session_id: str) -> ChatThread:
    thread = await ChatThread.create(user_id="u1", provider_session_id=session_id)
    message = await ChatMessage.create(
        role="user", thread_id=thread.id, parts=[{"type": "text", "text": "Design it"}]
    )
    await thread.connect(message, edge=CONTAINS)
    return thread


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [False, True])
async def test_native_amendment_replaces_typed_design_and_invalidates_old_approval(
    monkeypatch: pytest.MonkeyPatch, approved: bool
):
    """A real blueprint amendment invalidates approval for the previous draft."""
    thread = await _thread(f"native-amend-{approved}")
    thread.provider_id = "integral_native"
    await thread.save()
    first = await chat_threads.record_design_proposed(
        user_id="u1",
        session_id=thread.provider_session_id,
        summary="Bike Repair",
        proposal=_PROPOSAL,
        blueprint=_blueprint(),
    )
    thread = await ChatThread.get(thread.id)
    thread.design_proposed.update(approved=approved, affirm=approved)
    await thread.save()
    correction = await ChatMessage.create(
        role="user",
        thread_id=thread.id,
        parts=[{"type": "text", "text": "Remove the due date; the rest looks good."}],
    )
    await thread.connect(correction, edge=CONTAINS)

    async def legacy_judge(*args: object, **kwargs: object):
        raise AssertionError("Native proposal amendments must not invoke JV approval")

    monkeypatch.setattr(chat_threads, "looks_like_design_affirm", legacy_judge)
    amended = _blueprint()
    amended["tracks"][0]["entry_types"][0]["fields"].pop()
    result = await chat_threads.record_design_proposed(
        user_id="u1",
        session_id=thread.provider_session_id,
        summary="Bike Repair without a due date",
        proposal=_PROPOSAL.replace(", due date", "") + "No due date will be stored.",
        blueprint=amended,
    )
    assert result.get("ok") is True, result
    assert result["blueprint_revision"] == 2
    assert result["blueprint_digest"] != first["blueprint_digest"]
    current = await ChatThread.get(thread.id)
    assert current.design_proposed["approved"] is False
    assert "affirm" not in current.design_proposed
    assert current.design_proposed["proposed_at_user_turn"] == 2
    assert current.design_proposed.get("build_receipt") is None


@pytest.mark.asyncio
async def test_native_same_approved_blueprint_cannot_be_reproposed():
    """An identical approved blueprint cannot be reset to pending."""
    thread = await _thread("native-same-approved")
    thread.provider_id = "integral_native"
    await thread.save()
    await chat_threads.record_design_proposed(
        user_id="u1",
        session_id=thread.provider_session_id,
        summary="Bike Repair",
        proposal=_PROPOSAL,
        blueprint=_blueprint(),
    )
    thread = await ChatThread.get(thread.id)
    thread.design_proposed["approved"] = True
    await thread.save()
    result = await chat_threads.record_design_proposed(
        user_id="u1",
        session_id=thread.provider_session_id,
        summary="Another heading",
        proposal=_PROPOSAL,
        blueprint=_blueprint(),
    )
    assert result["error"] == "already_proposed"
    assert (await ChatThread.get(thread.id)).design_proposed["approved"] is True


@pytest.mark.asyncio
async def test_native_same_pending_blueprint_does_not_reset_approval_turn():
    """Saving an identical blueprint preserves its original approval turn."""
    thread = await _thread("native-same-pending")
    thread.provider_id = "integral_native"
    await thread.save()
    await chat_threads.record_design_proposed(
        user_id="u1",
        session_id=thread.provider_session_id,
        summary="Bike Repair",
        proposal=_PROPOSAL,
        blueprint=_blueprint(),
    )
    correction = await ChatMessage.create(
        role="user",
        thread_id=thread.id,
        parts=[{"type": "text", "text": "Looks good, build it."}],
    )
    await thread.connect(correction, edge=CONTAINS)

    result = await chat_threads.record_design_proposed(
        user_id="u1",
        session_id=thread.provider_session_id,
        summary="Bike Repair",
        proposal=_PROPOSAL,
        blueprint=_blueprint(),
    )

    assert result["error"] == "unchanged_design"
    marker = (await ChatThread.get(thread.id)).design_proposed
    assert marker["proposed_at_user_turn"] == 1
    assert marker["blueprint_revision"] == 1
    assert marker["approved"] is False


@pytest.mark.asyncio
async def test_propose_design_records_revisions_and_requires_blueprint_on_amend() -> (
    None
):
    """Persist proposal revisions and require a typed amendment blueprint."""
    thread = await _thread("bp-revisions")
    first = await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="bp-revisions",
        summary="Bike Repair",
        proposal=_PROPOSAL,
        blueprint=_blueprint(),
    )
    assert first["blueprint_revision"] == 1
    assert first["blueprint_digest"].startswith("sha256:")

    correction = await ChatMessage.create(
        role="user",
        thread_id=thread.id,
        parts=[{"type": "text", "text": "Drop the due date"}],
    )
    await thread.connect(correction, edge=CONTAINS)
    prose_only = await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="bp-revisions",
        summary="Bike Repair",
        proposal=_PROPOSAL,
    )
    assert prose_only["error"] == "blueprint_required"

    amended = _blueprint()
    del amended["tracks"][0]["entry_types"][0]["fields"][2]
    second = await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="bp-revisions",
        summary="Bike Repair",
        proposal=_PROPOSAL,
        blueprint=amended,
    )
    assert second["blueprint_revision"] == 2
    assert second["blueprint_diff"] == {
        "added": [],
        "removed": ["f.due"],
        "changed": [],
    }
    # The amend turn sees the prior blueprint so unchanged ids can be copied.
    assert "Prior blueprint (revision 1)" in second["prior_proposal"]
    assert '"id":"view.board"' in second["prior_proposal"]
    marker = (await ChatThread.get(thread.id)).design_proposed
    assert (
        marker["blueprint"]["tracks"][0]["entry_types"][0]["fields"][-1]["key"]
        == "status"
    )

    invalid = await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="bp-revisions",
        summary="Bike Repair",
        proposal=_PROPOSAL,
        blueprint={"app": {}},
    )
    assert invalid["error"] == "invalid_blueprint"


def test_fidelity_names_every_drift_deterministically() -> None:
    """Identify each mismatch between the approved design and build plan."""
    blueprint = _canonical()
    blueprint["dashboard"] = {
        "id": "dash",
        "name": "Shop",
        "widgets": [
            {
                "id": "w.due",
                "title": "Due",
                "type": "recent_entries",
                "decision": "Chase",
            }
        ],
    }
    operations = [(item["tool"], item["args"]) for item in _plan()]
    operations[1][1]["entry_types"][0]["fields"].pop()  # drop due_date
    operations[1][1]["entry_types"][0]["fields"].append(
        {"key": "notes", "name": "Notes", "type": "text"}
    )
    operations.append(
        (
            "integral_save_view",
            {"track_id": "{{track.id:Jobs}}", "name": "All", "view_type": "table"},
        )
    )

    errors = plan_fidelity_errors(blueprint, operations, new_app=True)
    assert errors == [
        "Track 'Jobs' omits approved field due_date (Due date).",
        "Track 'Jobs' adds field 'notes', which is not in the design.",
        "The plan adds table view 'all' on 'jobs', not in the design.",
        "The approved dashboard is missing from the plan.",
    ]
    assert (
        plan_fidelity_errors(
            blueprint | {"dashboard": None},
            [(i["tool"], i["args"]) for i in _plan()],
            new_app=True,
        )
        == []
    )


def test_fidelity_rejects_invented_seed_field_values() -> None:
    """Reject seed values that do not belong to the approved blueprint."""
    blueprint = _canonical()
    blueprint["seeds"] = [
        {"id": "seed.demo", "track": "track.jobs", "title": "Demo Job", "fields": {}}
    ]
    operations = [(item["tool"], item["args"]) for item in _plan()]
    seed = {
        "track_id": "{{track.id:Jobs}}",
        "title": "Demo Job",
        "fields": {"customer": "Example Customer"},
    }
    operations.append(("integral_create_entry", seed))

    assert any(
        "fields differ from the approved design" in error
        for error in plan_fidelity_errors(blueprint, operations, new_app=True)
    )
    seed["fields"] = {}
    assert plan_fidelity_errors(blueprint, operations, new_app=True) == []


def test_fidelity_refuses_open_decisions_and_unbuildable_constituents() -> None:
    """Keep unresolved decisions and unsupported items out of build plans."""
    blueprint = _canonical()
    ops = [(i["tool"], i["args"]) for i in _plan()]
    assert (
        "open decisions (q.billing)"
        in plan_fidelity_errors(
            blueprint
            | {"open_decisions": [{"id": "q.billing", "question": "Bill hourly?"}]},
            ops,
            new_app=True,
        )[0]
    )
    anchored = copy.deepcopy(blueprint)
    anchored["track_templates"] = [
        {
            "id": "tpl.details",
            "name": "Job details",
            "entry_types": [
                {
                    "id": "tpl.type.note",
                    "name": "Note",
                    "fields": [
                        {
                            "id": "tpl.f.body",
                            "key": "body",
                            "name": "Body",
                            "type": "text",
                        }
                    ],
                }
            ],
        }
    ]
    assert plan_fidelity_errors(anchored, ops, new_app=True) == [
        "The plan omits approved track template 'Job details'."
    ]
    anchored["track_templates"][0]["tag_groups"] = [
        {"id": "tpl.tags.kind", "name": "Kind", "tags": ["Photo"]}
    ]
    assert (
        "cannot yet create tags on track templates: tpl.details"
        in plan_fidelity_errors(anchored, ops, new_app=True)[0]
    )


@pytest.fixture
def approved(monkeypatch):
    """Provide an approved design and stub its chat affirmation boundary."""
    _reset_for_tests()
    thread = SimpleNamespace(
        user_id="user-1",
        design_proposed={
            "approved": True,
            "summary": "Bike Repair",
            "proposal": _PROPOSAL + "\nAlso a table of all jobs and a dashboard.",
            "blueprint": _canonical(),
            "blueprint_revision": 3,
            "blueprint_digest": "sha256:abc",
        },
    )

    async def affirmed(_session_id):
        return True

    async def get_thread(_session_id):
        return thread

    monkeypatch.setattr(scaffold_build, "design_chat_affirmed_for_build", affirmed)
    monkeypatch.setattr(scaffold_build, "get_thread_by_session", get_thread)
    yield thread
    _reset_for_tests()


def _context() -> Dict[str, Any]:
    return {
        "principal_id": "user-1",
        "scope": "ws-1",
        "session_id": "s-1",
        "interaction_id": "i-1",
    }


@pytest.mark.asyncio
async def test_build_with_dropped_field_fails_before_staging(
    approved, monkeypatch
) -> None:
    """Reject a plan that drops an approved field before staging writes."""
    from app.agentive.tooling import dispatch

    staged = []

    async def stage(tool, args, **_):
        staged.append(tool)
        return ToolResult(data={"batched": True})

    monkeypatch.setattr(dispatch, "dispatch_tool", stage)
    plan = _plan()
    plan[1]["args"]["entry_types"][0]["fields"].pop()
    result = await scaffold_build.build_approved_design(
        {"operations": plan}, **_context()
    )

    assert result.error_code == "plan_differs_from_design"
    assert "omits approved field due_date (Due date)" in result.message
    assert "plan repair" in result.message
    assert "not a request to re-approve the unchanged design" in result.message
    assert staged == []


@pytest.mark.asyncio
async def test_exact_blueprint_plan_builds_without_prose_extras(
    approved, monkeypatch
) -> None:
    """Build exactly the approved blueprint without prose-derived extras."""
    from app.agentive.tooling import dispatch

    staged, commits = [], []

    async def stage(tool, args, **_):
        staged.append(tool)
        return ToolResult(data={"batched": True})

    async def commit(name, args, **_):
        commits.append(args)
        return ToolResult(
            data={
                "_kind": "batch_applied",
                "applied": True,
                "token": "b-1",
                "execute_result": {"completed": 3, "total": 3},
            }
        )

    monkeypatch.setattr(dispatch, "dispatch_tool", stage)
    monkeypatch.setattr(dispatch, "_dispatch_batch_control", commit)
    result = await scaffold_build.build_approved_design(
        {"operations": _plan()}, **_context()
    )

    # The prose mentions a table and a dashboard; the blueprint has neither,
    # so neither is invented.
    assert staged == [
        "integral_create_app",
        "integral_create_app_track",
        "integral_save_view",
    ]
    assert commits[0]["allow_empty"] is True
    assert result.data["blueprint_revision"] == 3
    assert result.data["not_built"] == []


def test_propose_design_tool_schema_embeds_the_blueprint_contract() -> None:
    """Advertise the typed Integral blueprint schema on design tools."""
    from app.agentive.tooling.catalogue import _build_input_schema
    from app.agentive.tooling.manifest import load_manifest

    spec = load_manifest()["integral_propose_design"]
    schema = _build_input_schema(spec)
    blueprint = schema["properties"]["blueprint"]

    assert blueprint["type"] == "object"
    assert "tracks" in blueprint["required"]
    assert "BlueprintTrack" in schema["$defs"]
    assert "Once a design has a blueprint" in blueprint["description"]
