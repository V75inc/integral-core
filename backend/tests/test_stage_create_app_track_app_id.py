"""create_app_track coerces non-id app_id values to {{app.id}}."""

from __future__ import annotations

from app.agentive.tooling.bindings import (
    _normalize_in_batch_app_id,
    _stage_create_app_track,
)
from app.services.agent_scope import current_focused_app_id


def test_normalize_leaves_token_and_node_id():
    assert _normalize_in_batch_app_id("{{app.id}}") == "{{app.id}}"
    assert _normalize_in_batch_app_id("n.WorkspaceApp.abc") == "n.WorkspaceApp.abc"


def test_normalize_display_name_and_pending_to_positional():
    assert _normalize_in_batch_app_id("Car Rental Management") == "{{app.id}}"
    assert _normalize_in_batch_app_id("pending") == "{{app.id}}"
    assert _normalize_in_batch_app_id("{{app.id:pending}}") == "{{app.id}}"
    assert _normalize_in_batch_app_id("") == "{{app.id}}"


def test_stage_create_app_track_rewrites_name():
    token = current_focused_app_id.set("n.IntegralApp.real-app")
    try:
        staged = _stage_create_app_track(
            {
                "name": "Cars",
                "app_id": "pending",
                "description": "Fleet",
                "entry_types": [
                    {
                        "name": "Car",
                        "fields": [
                            {
                                "key": "registration",
                                "name": "Registration",
                                "type": "text",
                            }
                        ],
                    }
                ],
            }
        )
    finally:
        current_focused_app_id.reset(token)
    assert staged["payload"]["app_id"] == "n.IntegralApp.real-app"
    assert staged["kind"] == "create_track"
