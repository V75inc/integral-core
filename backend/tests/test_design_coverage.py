"""W1.4 design coverage: every blueprint item is classified against the live
palette, and integral_propose_design refuses what the substrate cannot build."""

from __future__ import annotations

import copy

import pytest

from app.models.edges import CONTAINS
from app.models.nodes import ChatMessage, ChatThread
from app.services import chat_threads
from app.services.design_coverage import check_design_coverage

_PROPOSAL = (
    "**Orders** app (fresh).\n\n"
    "- **Orders** — status, due date, customer\n"
    "- Views: Orders board by status, due-date calendar\n"
    "- Dashboard: order count\n"
)

_BLUEPRINT = {
    "app": {"id": "app", "name": "Orders"},
    "tracks": [
        {
            "id": "orders",
            "name": "Orders",
            "entry_types": [
                {
                    "name": "Order",
                    "fields": [
                        {
                            "key": "status",
                            "name": "Status",
                            "type": "select",
                            "options": ["new", "paid"],
                        },
                        {"key": "due", "name": "Due", "type": "date"},
                        {
                            "key": "parent",
                            "name": "Parent",
                            "type": "relation",
                            "relation": {
                                "target": "entry",
                                "target_entry_types": ["Order"],
                            },
                        },
                    ],
                }
            ],
        }
    ],
    "views": [
        {
            "id": "v.board",
            "track": "orders",
            "name": "Board",
            "type": "kanban",
            "decision": "What stage is each order in?",
            "config": {"group_by": "custom_fields.status"},
        },
        {
            "id": "v.cal",
            "track": "orders",
            "name": "Due dates",
            "type": "calendar",
            "decision": "What is due this week?",
            "config": {"date_field": "due"},
        },
    ],
    "dashboard": {
        "id": "dash",
        "name": "Overview",
        "widgets": [
            {"id": "w.count", "title": "Orders", "type": "kpi", "decision": "How many?"}
        ],
    },
}


def _variant(**changes):
    blueprint = copy.deepcopy(_BLUEPRINT)
    blueprint.update(changes)
    return blueprint


def _classes(result):
    return {row["id"]: row["class"] for row in result["items"]}


@pytest.mark.asyncio
async def test_native_design_is_buildable():
    result = await check_design_coverage("u1", _BLUEPRINT)
    assert result["status"] == "buildable"
    assert set(_classes(result).values()) == {"native"}
    widget = next(row for row in result["items"] if row["id"] == "w.count")
    assert "(metric_card)" in widget["requirement"]


@pytest.mark.asyncio
async def test_unknown_and_unbuildable_types_are_unsupported_with_alternatives():
    blueprint = copy.deepcopy(_BLUEPRINT)
    fields = blueprint["tracks"][0]["entry_types"][0]["fields"]
    fields += [
        {"key": "rating", "name": "Rating", "type": "stars"},
        {"key": "total", "name": "Total", "type": "computed"},
        {"key": "tier", "name": "Tier", "type": "select"},
    ]
    blueprint["views"].append(
        {
            "id": "v.map",
            "track": "orders",
            "name": "Map",
            "type": "map",
            "decision": "Where are orders?",
        }
    )
    blueprint["dashboard"]["widgets"].append(
        {"id": "w.heat", "title": "Heat", "type": "heatmap", "decision": "When?"}
    )
    result = await check_design_coverage("u1", blueprint)
    assert result["status"] == "unsupported"
    rows = {row["id"]: row for row in result["unsupported"]}
    assert set(rows) == {
        "orders.rating",
        "orders.total",
        "orders.tier",
        "v.map",
        "w.heat",
    }
    assert "available field types" in rows["orders.rating"]["detail"]
    assert "computed" not in rows["orders.rating"]["detail"].split(": ")[-1]
    assert "options" in rows["orders.tier"]["detail"]
    assert "kanban" in rows["v.map"]["detail"]
    assert "metric_card" in rows["w.heat"]["detail"]


@pytest.mark.asyncio
async def test_view_bindings_and_config_keys_follow_the_build():
    blueprint = copy.deepcopy(_BLUEPRINT)
    blueprint["views"] = [
        {
            "id": "v.board",
            "track": "orders",
            "name": "Board",
            "type": "kanban",
            "decision": "Stage?",
            "config": {"group_by": "due"},
        },
        {
            "id": "v.cal",
            "track": "orders",
            "name": "Cal",
            "type": "calendar",
            "decision": "Due?",
            "config": {"date_field": "status"},
        },
        {
            "id": "v.table",
            "track": "orders",
            "name": "Table",
            "type": "table",
            "decision": "All?",
            "config": {"columns": ["title", "custom_fields.missing"]},
        },
        {
            "id": "v.wiki",
            "track": "orders",
            "name": "Wiki",
            "type": "wiki",
            "decision": "Tree?",
            "config": {"parent_field": "parent", "colour": "red"},
        },
    ]
    result = await check_design_coverage("u1", blueprint)
    rows = {row["id"]: row["detail"] for row in result["unsupported"]}
    assert "select field" in rows["v.board"]
    assert "date field" in rows["v.cal"]
    assert "missing" in rows["v.table"]
    assert "colour" in rows["v.wiki"]


@pytest.mark.asyncio
async def test_operations_and_extension_views_need_a_trusted_package():
    blueprint = _variant(
        operations=[
            {"id": "op.charge", "name": "Charge card", "purpose": "Take payment"}
        ]
    )
    blueprint["views"].append(
        {
            "id": "v.ext",
            "track": "orders",
            "name": "Route planner",
            "type": "extension_view",
            "decision": "Which route?",
        }
    )
    result = await check_design_coverage("u1", blueprint)
    assert result["status"] == "needs_trusted_package"
    names = {row["name"] for row in result["requires_trusted_package"]}
    assert names == {"Charge card", "Route planner"}


@pytest.mark.asyncio
async def test_invalid_blueprint_is_an_error():
    result = await check_design_coverage("u1", {"app": {"id": "app"}})
    assert result["error"] == "invalid_blueprint"


async def _thread(session_id: str) -> ChatThread:
    thread = await ChatThread.create(user_id="u1", provider_session_id=session_id)
    msg = await ChatMessage.create(role="user", thread_id=thread.id)
    await thread.connect(msg, edge=CONTAINS)
    return thread


@pytest.mark.asyncio
async def test_propose_refuses_unsupported_design_before_recording():
    thread = await _thread("coverage-unsupported")
    blueprint = copy.deepcopy(_BLUEPRINT)
    blueprint["views"][0]["type"] = "map"
    result = await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="coverage-unsupported",
        summary="Orders app",
        proposal=_PROPOSAL,
        blueprint=blueprint,
    )
    assert result["error"] == "unsupported_design"
    assert "unknown view type" in result["detail"]
    assert not (await ChatThread.get(thread.id)).design_proposed


@pytest.mark.asyncio
async def test_propose_refuses_a_promised_code_effect_with_no_operation(monkeypatch):
    async def texts(text, *, workspace_id=None, agent_id=None):
        return "texts customers" if "texts customers" in text.casefold() else ""

    monkeypatch.setattr(chat_threads, "_proposal_promises_unbuilt_effect", texts)
    thread = await _thread("coverage-sms")
    promise = _PROPOSAL + "- Automatically texts customers when the cake is ready.\n"
    result = await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="coverage-sms",
        summary="Orders app",
        proposal=promise,
        blueprint=copy.deepcopy(_BLUEPRINT),
    )
    assert result["error"] == "code_needs_unlisted"
    assert "texts customers" in result["detail"]
    assert not (await ChatThread.get(thread.id)).design_proposed


@pytest.mark.asyncio
async def test_propose_requires_code_needs_named():
    thread = await _thread("coverage-package")
    blueprint = _variant(
        operations=[
            {"id": "op.charge", "name": "Charge card", "purpose": "Take payment"}
        ]
    )
    refused = await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="coverage-package",
        summary="Orders app",
        proposal=_PROPOSAL,
        blueprint=blueprint,
    )
    assert refused["error"] == "trusted_package_unnamed"
    assert "Charge card" in refused["detail"]
    assert not (await ChatThread.get(thread.id)).design_proposed

    named = _PROPOSAL + "- Charge card needs a trusted package (custom code).\n"
    result = await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="coverage-package",
        summary="Orders app",
        proposal=named,
        blueprint=blueprint,
    )
    assert result["ok"] is True
    assert result["coverage"]["status"] == "needs_trusted_package"
    marker = (await ChatThread.get(thread.id)).design_proposed
    assert marker["coverage"]["requires_trusted_package"][0]["name"] == "Charge card"
