"""W1.1 tags in the build: inline vocabulary, tagged seeds, tag-filtered views."""

from __future__ import annotations

import copy
from typing import Any, Dict

import pytest

from app.agentive.batch_validation import validate_batch_references
from app.agentive.staging import StagingError
from app.services.design_blueprint import plan_fidelity_errors, validate_blueprint
from app.services.operational_model_authoring import normalize_inline_taxonomy

pytestmark = pytest.mark.smoke

_FIELDS = [
    {"key": "customer", "name": "Customer", "type": "text"},
    {"key": "status", "name": "Status", "type": "select", "enum": ["Queued", "Done"]},
]


def _blueprint() -> Dict[str, Any]:
    return {
        "app": {"id": "app", "name": "Bike Repair"},
        "tracks": [
            {
                "id": "track.jobs",
                "name": "Jobs",
                "entry_types": [{"name": "Job", "fields": copy.deepcopy(_FIELDS)}],
                "tag_groups": [{"name": "Priority", "tags": ["Urgent", "Routine"]}],
            }
        ],
        "views": [
            {
                "id": "view.urgent",
                "track": "track.jobs",
                "name": "Urgent jobs",
                "type": "table",
                "decision": "Which jobs jump the queue",
            }
        ],
        "seeds": [
            {
                "id": "seed.flat",
                "track": "track.jobs",
                "title": "Flat tyre",
                "fields": {"customer": "Ana", "status": "Queued"},
                "tags": ["Urgent"],
            },
            {
                "id": "seed.tune",
                "track": "track.jobs",
                "title": "Annual tune-up",
                "fields": {"customer": "Ben", "status": "Queued"},
                "tags": ["Routine"],
            },
        ],
    }


def _plan() -> list:
    return [
        {"tool": "integral_create_app", "args": {"name": "Bike Repair"}},
        {
            "tool": "integral_create_app_track",
            "args": {
                "name": "Jobs",
                "app_id": "{{app.id}}",
                "entry_types": [{"name": "Job", "fields": copy.deepcopy(_FIELDS)}],
                "taxonomy": {
                    "tag_groups": [{"name": "Priority", "tags": ["Urgent", "Routine"]}]
                },
            },
        },
        {
            "tool": "integral_save_view",
            "args": {
                "track_id": "{{track.id:Jobs}}",
                "name": "Urgent jobs",
                "view_type": "table",
                "config": {
                    "columns": ["custom_fields.customer"],
                    "filters": [
                        {
                            "field": "tags",
                            "operator": "eq",
                            "value": "{{tag.id:Urgent}}",
                        }
                    ],
                },
            },
        },
        {
            "tool": "integral_create_entry",
            "args": {
                "track_id": "{{track.id:Jobs}}",
                "title": "Flat tyre",
                "entry_type": "Job",
                "fields": {"customer": "Ana", "status": "Queued"},
                "tags": ["Urgent"],
            },
        },
        {
            "tool": "integral_create_entry",
            "args": {
                "track_id": "{{track.id:Jobs}}",
                "title": "Annual tune-up",
                "entry_type": "Job",
                "fields": {"customer": "Ben", "status": "Queued"},
                "tags": ["Routine"],
            },
        },
    ]


def _canonical() -> Dict[str, Any]:
    blueprint, error = validate_blueprint(_blueprint())
    assert error is None, error
    return blueprint


def _ops(plan: list) -> list:
    out = []
    for item in plan:
        args = dict(item["args"])
        if args.get("taxonomy"):
            args["taxonomy"] = {
                "tag_groups": normalize_inline_taxonomy(args["taxonomy"])
            }
        out.append((item["tool"], args))
    return out


def test_inline_taxonomy_normalizes_and_rejects_ambiguous_vocabularies() -> None:
    assert normalize_inline_taxonomy(
        {
            "tag_groups": [
                {
                    "name": "Service Area",
                    "tags": ["Wheels", {"name": "Spokes", "parent": "Wheels"}],
                }
            ]
        }
    ) == [
        {
            "key": "service_area",
            "name": "Service Area",
            "tags": [{"name": "Wheels"}, {"name": "Spokes", "parent": "Wheels"}],
        }
    ]
    for bad, message in [
        (
            {
                "tag_groups": [
                    {"name": "A", "tags": ["X"]},
                    {"name": "B", "tags": ["x"]},
                ]
            },
            "declared twice",
        ),
        (
            {"tag_groups": [{"name": "A", "tags": [{"name": "Y", "parent": "Z"}]}]},
            "earlier tag",
        ),
        ({"tag_groups": [{"name": "A", "tags": []}]}, "at least one tag"),
        (["Urgent"], "must be an object"),
        ("Urgent", "tag_groups"),
    ]:
        with pytest.raises(ValueError, match=message):
            normalize_inline_taxonomy(bad)
    # The bare group list the model first reached for in the browser smoke.
    assert normalize_inline_taxonomy([{"name": "Priority", "tags": ["Urgent"]}]) == [
        {"key": "priority", "name": "Priority", "tags": [{"name": "Urgent"}]}
    ]


def test_blueprint_seed_tags_must_come_from_the_track_vocabulary() -> None:
    blueprint = _blueprint()
    blueprint["seeds"][0]["tags"] = ["Emergency"]
    _, error = validate_blueprint(blueprint)
    assert "tags not in its track's tag_groups: Emergency" in error
    canonical = _canonical()
    assert canonical["tracks"][0]["tag_groups"][0]["id"] == "track.jobs.tags.priority"


def test_fidelity_compares_tag_vocabulary_and_seed_tags() -> None:
    blueprint = _canonical()
    assert plan_fidelity_errors(blueprint, _ops(_plan()), new_app=True) == []

    plan = _plan()
    plan[1]["args"]["taxonomy"]["tag_groups"][0]["tags"] = ["Urgent", "Someday"]
    plan[3]["args"]["tags"] = []
    errors = plan_fidelity_errors(blueprint, _ops(plan), new_app=True)
    assert "Track 'Jobs' omits approved tag 'Routine' (Priority)." in errors
    assert "Track 'Jobs' adds tag 'someday', which is not in the design." in errors
    assert "Seed 'Flat tyre' must carry tags ['urgent'], not []." in errors

    # A tag created as its own operation counts, but must sit in its group.
    plan = _plan()
    plan[1]["args"]["taxonomy"]["tag_groups"][0]["tags"] = ["Urgent"]
    plan.insert(
        2,
        {
            "tool": "integral_create_tag",
            "args": {
                "name": "Routine",
                "track_id": "{{track.id:Jobs}}",
                "group_key": "kind",
            },
        },
    )
    assert plan_fidelity_errors(blueprint, _ops(plan), new_app=True) == [
        "Tag 'Routine' on 'Jobs' belongs to group 'Priority'."
    ]


def test_inline_tags_are_named_batch_references() -> None:
    from app.agentive.tooling.bindings import _stage_create_track

    track = _stage_create_track(
        {**_plan()[1]["args"], "title": "Jobs", "app_id": "{{app.id}}"}
    )
    assert "- **Tags (Priority):** Urgent, Routine" in track["diff_human"]
    ops = [
        {"kind": "create_app", "payload": {"name": "Bike Repair"}},
        {"kind": "create_app_track", "payload": track["payload"]},
        {
            "kind": "save_view",
            "payload": {
                "track_id": "{{track.id:Jobs}}",
                "config": {
                    "filters": [{"field": "tags", "value": "{{tag.id:Urgent}}"}]
                },
            },
        },
    ]
    validate_batch_references(ops)

    ops[2]["payload"]["config"]["filters"][0]["value"] = "{{tag.id:Missing}}"
    with pytest.raises(StagingError, match="unresolved references"):
        validate_batch_references(ops)


@pytest.mark.asyncio
async def test_approved_design_builds_vocabulary_tagged_seeds_and_tag_view(
    authenticated_client, test_user
) -> None:
    from app.agentive import staging
    from app.agentive.tooling.dispatch import dispatch_tool
    from app.models.edges import CONTAINS
    from app.models.nodes import ChatMessage, ChatThread, Entry, Tag, Track, View
    from app.services.app_graph import get_track_attached_operational_model
    from app.services.chat_threads import stamp_design_approved

    staging._reset_for_tests()
    response = await authenticated_client.post(
        "/api/workspaces", json={"name": "Tag build"}
    )
    assert response.status_code == 200, response.text
    ws = response.json()["workspace"]["id"]
    sid = "tag-build"
    uid = getattr(test_user, "user_id", None) or test_user.id
    thread = await ChatThread.create(
        user_id=uid, workspace_id=ws, provider_session_id=sid
    )

    async def say(content):
        msg = await ChatMessage.create(
            role="user", thread_id=thread.id, content=content
        )
        await thread.connect(msg, edge=CONTAINS)

    await say("Design a bike repair app")

    async def call(tool, **args):
        return await dispatch_tool(
            tool, args, principal_id=uid, scope=ws, session_id=sid
        )

    proposed = await call(
        "integral_propose_design",
        summary="Bike Repair",
        proposal=(
            "**Bike Repair** app with one **Jobs** track (customer, status). Jobs "
            "are tagged by Priority (Urgent, Routine). An Urgent jobs table shows "
            "which jobs jump the queue. Seeds: Flat tyre (Urgent), Annual tune-up."
        ),
        blueprint=_blueprint(),
    )
    assert not proposed.is_error, proposed
    await say("Looks good, build it")
    thread = await ChatThread.get(thread.id)
    assert await stamp_design_approved(thread=thread, utterance="Looks good, build it")

    built = await call("integral_build_approved_design", operations=_plan())
    assert not built.is_error, built
    assert built.data["applied"] is True, built.data
    staging._reset_for_tests()

    track = next(
        t for t in await Track.find({"context.title": "Jobs"}) if t.workspace_id == ws
    )
    tags = {t.name: t for t in await Tag.find({"context.track_id": track.id})}
    assert set(tags) == {"Urgent", "Routine"}
    assert {t.group_key for t in tags.values()} == {"priority"}
    model = await get_track_attached_operational_model(track)
    attached = await model.nodes(edge=[CONTAINS], node=["Tag"])
    assert {t.name for t in attached} == {"Urgent", "Routine"}
    taxonomy = model.manifest["track"]["taxonomy"]["tag_groups"]
    assert [g["key"] for g in taxonomy] == ["priority"]

    seeds = {e.title: e for e in await Entry.find({"context.track_id": track.id})}
    assert seeds["Flat tyre"].tags == [tags["Urgent"].id]
    assert seeds["Annual tune-up"].tags == [tags["Routine"].id]

    view = next(
        v
        for v in await View.find({"context.track_id": track.id})
        if v.name == "Urgent jobs"
    )
    listed = await authenticated_client.get(
        "/api/entries",
        params={"track_id": track.id, "view_id": view.id},
        headers={"X-Integral-Scope": f"ws:{ws}"},
    )
    assert listed.status_code == 200, listed.text
    assert [e["title"] for e in listed.json()["entries"]] == ["Flat tyre"]
