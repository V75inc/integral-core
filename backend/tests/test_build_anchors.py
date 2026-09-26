"""W1.2 anchors in the build: templates registered, detail Tracks per parent."""

from __future__ import annotations

import copy
from typing import Any, Dict

import pytest

from app.services.design_blueprint import plan_fidelity_errors, validate_blueprint

pytestmark = pytest.mark.smoke

_PROJECT_FIELDS = [
    {"key": "client", "name": "Client", "type": "text"},
    {
        "key": "details",
        "name": "Details",
        "type": "relation",
        "relation": {"target": "track", "target_track_template": "tpl.details"},
    },
]
_NOTE_FIELDS = [{"key": "body", "name": "Body", "type": "text"}]


def _blueprint() -> Dict[str, Any]:
    return {
        "app": {"id": "app", "name": "Studio"},
        "tracks": [
            {
                "id": "track.projects",
                "name": "Projects",
                "entry_types": [
                    {"name": "Project", "fields": copy.deepcopy(_PROJECT_FIELDS)}
                ],
            }
        ],
        "track_templates": [
            {
                "id": "tpl.details",
                "name": "Project Details",
                "entry_types": [
                    {"name": "Note", "fields": copy.deepcopy(_NOTE_FIELDS)}
                ],
            }
        ],
        "seeds": [
            {
                "id": "seed.site",
                "track": "track.projects",
                "title": "Website",
                "fields": {"client": "Acme"},
            },
            {
                "id": "seed.brand",
                "track": "track.projects",
                "title": "Brand refresh",
                "fields": {"client": "Globex"},
            },
        ],
    }


def _plan_project_fields() -> list:
    fields = copy.deepcopy(_PROJECT_FIELDS)
    fields[1]["relation"] = {
        "target": "track",
        "target_track_template": "project_details",
    }
    return fields


def _plan() -> list:
    return [
        {"tool": "integral_create_app", "args": {"name": "Studio"}},
        {
            "tool": "integral_register_track_template",
            "args": {
                "app_id": "{{app.id}}",
                "name": "Project Details",
                "entry_types": [
                    {"name": "Note", "fields": copy.deepcopy(_NOTE_FIELDS)}
                ],
            },
        },
        {
            "tool": "integral_create_app_track",
            "args": {
                "name": "Projects",
                "app_id": "{{app.id}}",
                "entry_types": [{"name": "Project", "fields": _plan_project_fields()}],
            },
        },
        {
            "tool": "integral_create_entry",
            "args": {
                "track_id": "{{track.id:Projects}}",
                "title": "Website",
                "entry_type": "Project",
                "fields": {"client": "Acme"},
            },
        },
        {
            "tool": "integral_create_entry",
            "args": {
                "track_id": "{{track.id:Projects}}",
                "title": "Brand refresh",
                "entry_type": "Project",
                "fields": {"client": "Globex"},
            },
        },
    ]


def _canonical() -> Dict[str, Any]:
    blueprint, error = validate_blueprint(_blueprint())
    assert error is None, error
    return blueprint


def _ops(plan: list) -> list:
    return [(item["tool"], dict(item["args"])) for item in plan]


def test_blueprint_template_is_anchored_and_not_also_a_track() -> None:
    blueprint = _blueprint()
    blueprint["tracks"][0]["entry_types"][0]["fields"].pop()
    _, error = validate_blueprint(blueprint)
    assert "track template tpl.details is anchored by no field" in error

    blueprint = _blueprint()
    blueprint["tracks"].append(
        {
            "id": "track.details",
            "name": "Project Details",
            "entry_types": [{"name": "Note", "fields": copy.deepcopy(_NOTE_FIELDS)}],
        }
    )
    _, error = validate_blueprint(blueprint)
    assert "Project Details: a track template is not also a Track" in error

    blueprint = _blueprint()
    blueprint["views"] = [
        {
            "id": "view.notes",
            "track": "tpl.details",
            "name": "Notes",
            "type": "table",
            "decision": "Read a project's notes",
        }
    ]
    _, error = validate_blueprint(blueprint)
    assert "view view.notes: tpl.details is a track template" in error

    # Relation config the build tools accept is not refused in the blueprint.
    blueprint = _blueprint()
    blueprint["tracks"][0]["entry_types"][0]["fields"].append(
        {
            "key": "lead",
            "name": "Lead",
            "type": "relation",
            "relation": {
                "target": "entry",
                "target_entry_types": ["Project"],
                "target_track_types": ["Projects"],
                "allow_cross_track": False,
            },
        }
    )
    assert validate_blueprint(blueprint)[1] is None


def test_plan_view_shorthand_and_item_id_track_refs_are_accepted() -> None:
    """Shapes the model sent in the natural-language browser smoke."""
    from app.agentive.tooling.scaffold_build import _approved_plan_item

    view = _approved_plan_item(
        {
            "tool": "integral_save_view",
            "args": {
                "track_id": "{{track.id:Projects}}",
                "name": "All projects",
                "type": "table",
                "decision": "Show every project",
                "config": {},
            },
        }
    )["args"]
    assert view["view_type"] == "table"
    assert "type" not in view and "decision" not in view


def test_fidelity_compares_templates_and_anchor_targets() -> None:
    blueprint = _canonical()
    assert plan_fidelity_errors(blueprint, _ops(_plan()), new_app=True) == []

    plan = _plan()
    plan[2]["args"]["entry_types"][0]["fields"][1]["relation"][
        "target_track_template"
    ] = "other"
    plan[1]["args"]["entry_types"][0]["fields"].append(
        {"key": "due", "name": "Due", "type": "date"}
    )
    errors = plan_fidelity_errors(blueprint, _ops(plan), new_app=True)
    assert (
        "Field details must anchor to track template 'project_details' "
        "with relation.target='track'." in errors
    )
    assert (
        "Track template 'Project Details' adds field 'due', which is not in the design."
        in errors
    )

    del plan[1]
    assert (
        "The plan omits approved track template 'Project Details'."
        in plan_fidelity_errors(blueprint, _ops(plan), new_app=True)
    )


def test_flat_relation_keys_fold_under_relation() -> None:
    """The browser smoke model put target/target_track_template on the field."""
    from app.agentive.tooling.scaffold_build import _approved_plan_item

    item = copy.deepcopy(_plan()[2])
    field = item["args"]["entry_types"][0]["fields"][1]
    item["args"]["entry_types"][0]["fields"][1] = {
        "key": field["key"],
        "name": field["name"],
        "type": "relation",
        **field["relation"],
    }
    folded = _approved_plan_item(item)["args"]["entry_types"][0]["fields"][1]
    assert folded == _plan()[2]["args"]["entry_types"][0]["fields"][1]


def test_register_template_stager_normalizes_the_in_batch_app_ref() -> None:
    from app.agentive.batch_validation import validate_batch_references
    from app.agentive.tooling.bindings import _stage_register_track_template

    staged = _stage_register_track_template(_plan()[1]["args"])
    assert staged["kind"] == "register_track_template"
    assert staged["payload"]["app_id"] == "{{app.id}}"
    assert "- **Entry types:** Note" in staged["diff_human"]
    validate_batch_references(
        [
            {"kind": "create_app", "payload": {"name": "Studio"}},
            {"kind": "register_track_template", "payload": staged["payload"]},
        ]
    )
    with pytest.raises(ValueError):
        _stage_register_track_template({**_plan()[1]["args"], "entry_types": []})


async def _approved_thread(authenticated_client, test_user, name: str):
    from app.agentive import staging
    from app.agentive.tooling.dispatch import dispatch_tool
    from app.models.edges import CONTAINS
    from app.models.nodes import ChatMessage, ChatThread
    from app.services.chat_threads import stamp_design_approved

    staging._reset_for_tests()
    response = await authenticated_client.post("/api/workspaces", json={"name": name})
    assert response.status_code == 200, response.text
    ws = response.json()["workspace"]["id"]
    sid = f"anchor-{name}"
    uid = getattr(test_user, "user_id", None) or test_user.id
    thread = await ChatThread.create(
        user_id=uid, workspace_id=ws, provider_session_id=sid
    )

    async def say(content):
        msg = await ChatMessage.create(
            role="user", thread_id=thread.id, content=content
        )
        await thread.connect(msg, edge=CONTAINS)

    async def call(tool, **args):
        return await dispatch_tool(
            tool, args, principal_id=uid, scope=ws, session_id=sid
        )

    async def approve():
        await say("Looks good, build it")
        fresh = await ChatThread.get(thread.id)
        assert await stamp_design_approved(
            thread=fresh, utterance="Looks good, build it"
        )

    await say("Design a studio app")
    return ws, call, approve


_PROPOSAL = (
    "**Studio** app with one **Projects** track (client). Each project gets its "
    "own **Project Details** track of notes, created when the project is filed."
)


@pytest.mark.asyncio
async def test_two_parents_get_distinct_detail_tracks_from_one_template(
    authenticated_client, test_user
) -> None:
    from app.agentive import staging
    from app.models.nodes import App, Entry, Track
    from app.services.app_graph import get_app_attached_operational_model

    ws, call, approve = await _approved_thread(
        authenticated_client, test_user, "Anchor build"
    )
    proposed = await call(
        "integral_propose_design",
        summary="Studio",
        proposal=_PROPOSAL + " Seeds: Website (Acme), Brand refresh (Globex).",
        blueprint=_blueprint(),
    )
    assert not proposed.is_error, proposed
    await approve()

    plan = _plan()
    plan[3]["args"]["track_id"] = "{{track.id:track.projects}}"
    built = await call("integral_build_approved_design", operations=plan)
    assert not built.is_error, built
    assert built.data["applied"] is True, built.data
    staging._reset_for_tests()

    app = next(
        a for a in await App.find({"context.name": "Studio"}) if a.workspace_id == ws
    )
    model = await get_app_attached_operational_model(app)
    assert [t["key"] for t in model.manifest["app"]["track_templates"]] == [
        "project_details"
    ]
    projects = next(
        t
        for t in await Track.find({"context.title": "Projects"})
        if t.workspace_id == ws
    )
    seeds = {e.title: e for e in await Entry.find({"context.track_id": projects.id})}
    detail_ids = {title: e.custom_fields.get("details") for title, e in seeds.items()}
    assert set(detail_ids) == {"Website", "Brand refresh"}
    assert all(detail_ids.values()) and len(set(detail_ids.values())) == 2
    for detail_id in detail_ids.values():
        detail = await Track.get(detail_id)
        assert detail is not None and detail.workspace_id == ws


@pytest.mark.asyncio
async def test_empty_app_registers_the_template_without_a_shared_detail_track(
    authenticated_client, test_user
) -> None:
    from app.agentive import staging
    from app.models.nodes import App, Track
    from app.services.app_graph import get_app_attached_operational_model
    from app.services.operational_model_graph import sync_attached_manifest

    blueprint = _blueprint()
    blueprint["seeds"] = []
    ws, call, approve = await _approved_thread(
        authenticated_client, test_user, "Anchor empty"
    )
    proposed = await call(
        "integral_propose_design",
        summary="Studio",
        proposal=_PROPOSAL + " No sample records.",
        blueprint=blueprint,
    )
    assert not proposed.is_error, proposed
    await approve()

    built = await call("integral_build_approved_design", operations=_plan()[:3])
    assert not built.is_error, built
    assert built.data["applied"] is True, built.data
    staging._reset_for_tests()

    tracks = [t for t in await Track.find({}) if t.workspace_id == ws]
    assert [t.title for t in tracks] == ["Projects"]
    app = next(
        a for a in await App.find({"context.name": "Studio"}) if a.workspace_id == ws
    )
    model = await get_app_attached_operational_model(app)
    await sync_attached_manifest(model)
    model = await get_app_attached_operational_model(app)
    assert [t["key"] for t in model.manifest["app"]["track_templates"]] == [
        "project_details"
    ]


@pytest.mark.asyncio
async def test_template_registration_is_hoisted_ahead_of_anchoring_tracks(
    authenticated_client, test_user
) -> None:
    ws, call, approve = await _approved_thread(
        authenticated_client, test_user, "Anchor order"
    )
    proposed = await call(
        "integral_propose_design",
        summary="Studio",
        proposal=_PROPOSAL + " Seeds: Website (Acme), Brand refresh (Globex).",
        blueprint=_blueprint(),
    )
    assert not proposed.is_error, proposed
    await approve()

    plan = _plan()
    plan[3]["args"]["fields"]["details"] = "n.Track.x"
    refused = await call("integral_build_approved_design", operations=plan)
    assert refused.is_error
    assert "sets anchored field(s) details" in refused.message

    plan = _plan()
    plan[1], plan[2] = plan[2], plan[1]
    built = await call("integral_build_approved_design", operations=plan)
    assert not built.is_error, built
    assert built.data["applied"] is True, built.data


@pytest.mark.asyncio
async def test_member_seed_for_the_requesting_user(
    authenticated_client, test_user
) -> None:
    """'Put me as the owner' — the browser smoke model wrote {{user.id}}."""
    from app.agentive import staging
    from app.models.nodes import Entry, Track

    owner = {"key": "owner", "name": "Owner", "type": "member"}
    blueprint = _blueprint()
    blueprint["tracks"][0]["entry_types"][0]["fields"].append(dict(owner))
    ws, call, approve = await _approved_thread(
        authenticated_client, test_user, "Anchor owner"
    )
    proposed = await call(
        "integral_propose_design",
        summary="Studio",
        proposal=_PROPOSAL + " Seeds: Website (Acme), Brand refresh (Globex).",
        blueprint=blueprint,
    )
    assert not proposed.is_error, proposed
    await approve()

    plan = _plan()
    plan[2]["args"]["entry_types"][0]["fields"].append(dict(owner))
    plan[3]["args"]["fields"]["owner"] = "{{user.id}}"
    plan[4]["args"]["fields"]["owner"] = test_user.user_id
    built = await call("integral_build_approved_design", operations=plan)
    assert not built.is_error, built
    assert built.data["applied"] is True, built.data
    staging._reset_for_tests()

    projects = next(
        t
        for t in await Track.find({"context.title": "Projects"})
        if t.workspace_id == ws
    )
    owners = {
        e.title: e.custom_fields.get("owner")
        for e in await Entry.find({"context.track_id": projects.id})
    }
    assert owners == {"Website": test_user.id, "Brand refresh": test_user.id}


def test_blueprint_folds_anchor_keys_written_on_the_field() -> None:
    """Shapes the model sent while revising the design in the browser smoke."""
    for anchor in (
        {"target": "track", "target_track_template": "tpl.details"},
        {"config": {"target": "track", "target_track_template": "tpl.details"}},
        {"relation": {"target": "track", "track_template": "tpl.details"}},
        {"track_template": "tpl.details"},
    ):
        blueprint = _blueprint()
        blueprint["tracks"][0]["entry_types"][0]["fields"][1] = {
            "key": "details",
            "name": "Details",
            "type": "relation",
            **anchor,
        }
        canonical, error = validate_blueprint(blueprint)
        assert error is None, (anchor, error)
        relation = canonical["tracks"][0]["entry_types"][0]["fields"][1]["relation"]
        assert relation["target"] == "track"
        assert relation["target_track_template"] == "tpl.details"
