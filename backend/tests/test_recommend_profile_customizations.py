from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.nodes import Entry, Track
from app.services import app_graph
from app.services import operational_model_authoring as authoring
from app.services import permissions


@pytest.mark.asyncio
async def test_recommender_uses_repeated_body_patterns_and_model_field_types(
    monkeypatch,
):
    entries = [
        SimpleNamespace(
            id=f"entry-{index}",
            title=f"Request {index}",
            body=(
                f"Priority: {priority}\nCustomer: {customer}\n"
                f"Reference: {reference}"
            ),
            entry_type_key="request",
            fields={},
        )
        for index, (priority, customer, reference) in enumerate(
            (
                (
                    "High" if index % 2 == 0 else "Low",
                    "Acme" if index % 2 == 0 else "Globex",
                    f"REF-{index:03d}",
                )
                for index in range(10)
            )
        )
    ]
    entries.extend(
        [
            SimpleNamespace(
                id="contact-acme",
                title="Acme",
                body="",
                entry_type_key="contact",
                fields={},
            ),
            SimpleNamespace(
                id="contact-globex",
                title="Globex",
                body="",
                entry_type_key="contact",
                fields={},
            ),
        ]
    )
    track = SimpleNamespace(id="track-1")
    cp = SimpleNamespace(
        manifest={
            "track": {
                "entry_types": [
                    {
                        "key": "request",
                        "fields": [
                            {"key": "due", "type": "date"},
                            {
                                "key": "state",
                                "type": "select",
                                "enum": ["Open", "Done"],
                            },
                        ],
                    }
                ],
                "views": [],
            }
        }
    )

    async def get_track(_track_id):
        return track

    async def find_entries(_query):
        return entries

    async def attached_model(_track):
        return cp

    async def role(_user_id, _resource_type, _resource_id):
        return "owner"

    monkeypatch.setattr(Track, "get", staticmethod(get_track))
    monkeypatch.setattr(Entry, "find", staticmethod(find_entries))
    monkeypatch.setattr(
        app_graph, "get_track_attached_operational_model", attached_model
    )
    monkeypatch.setattr(permissions, "resolve_role", role)

    result = await authoring.recommend_profile_customizations("user-1", "track-1")
    proposals = {
        suggestion["patch_args"].get("field", {}).get("key"): suggestion
        for suggestion in result["suggestions"]
        if suggestion["op"] == "add_field"
    }
    assert proposals["priority"]["patch_args"]["field"]["type"] == "select"
    assert proposals["priority"]["patch_args"]["field"]["enum"] == ["High", "Low"]
    assert proposals["customer"]["patch_args"]["field"]["type"] == "relation"
    assert proposals["customer"]["patch_args"]["field"]["relation"][
        "target_entry_types"
    ] == ["contact"]
    assert proposals["reference"]["patch_args"]["field"]["type"] == "text"

    views = [
        suggestion["patch_args"]["spec"]
        for suggestion in result["suggestions"]
        if suggestion["op"] == "add_view"
    ]
    assert {view["type"] for view in views} == {"table", "calendar", "kanban"}
    calendar = next(view for view in views if view["type"] == "calendar")
    assert calendar["config"]["calendar_mapping"]["dateField"] == "custom_fields.due"
    board = next(view for view in views if view["type"] == "kanban")
    assert board["config"]["group_by"] == "custom_fields.state"
