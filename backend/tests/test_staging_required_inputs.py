"""Proposal validation must collect missing inputs without executing defaults."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive.tooling import bindings
from app.agentive.tooling.stagers_filing import (
    _missing_required_fields,
    missing_create_inputs,
)


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_explicit_empty_does_not_satisfy_required_or_use_literal_default(empty):
    schema = {
        "fields": [
            {"key": "team", "name": "Team", "required": True, "default": "Operations"}
        ]
    }
    assert _missing_required_fields(schema, {}) == []
    assert _missing_required_fields(schema, {"team": empty}) == [("team", "Team")]


def test_required_defaults_are_declared_and_do_not_hide_other_missing_inputs():
    schema = {
        "fields": [
            {"key": "count", "required": True, "default": 0},
            {"key": "enabled", "required": True, "default": False},
            {
                "key": "code",
                "required": True,
                "validation": {"preview_default_tool": "app.next_number"},
            },
            {"key": "computed", "required": True, "type": "computed"},
            {
                "key": "customer",
                "required": True,
                "type": "relation",
                "relation": {"target": "entry"},
                "validation": {
                    "create_from_label": True,
                    "write_label_to": "customer_name",
                },
            },
            {
                "key": "ledger",
                "required": True,
                "type": "relation",
                "relation": {
                    "target": "track",
                    "auto_provision": True,
                    "target_track_template": "ledger",
                },
            },
            {"key": "team", "name": "Team", "required": True},
            {"key": "role", "name": "Role", "required": True},
        ]
    }
    assert _missing_required_fields(schema, {"customer_name": "Sample company"}) == [
        ("team", "Team"),
        ("role", "Role"),
    ]
    assert ("customer", "customer") in _missing_required_fields(schema, {})


@pytest.mark.asyncio
async def test_settings_are_read_and_missing_keys_are_not_assumed_defaulted(
    monkeypatch,
):
    schema = {
        "fields": [
            {"key": "currency", "required": True, "default_from_setting": "currency"},
            {"key": "team", "required": True, "default_from_setting": "team"},
        ]
    }
    monkeypatch.setattr(
        "app.services.operational_model_runtime.resolve_track_runtime_profile",
        AsyncMock(return_value=(None, schema, None)),
    )
    monkeypatch.setattr(
        "app.services.operational_model_entry_fields.resolve_entry_type_spec",
        lambda *_: schema,
    )
    settings = AsyncMock(
        side_effect=lambda _, key: "USD" if key == "currency" else None
    )
    monkeypatch.setattr(
        "app.services.operational_model_entry_fields._resolve_setting_default", settings
    )
    tool = AsyncMock(
        side_effect=AssertionError("A preview must not invoke an App tool")
    )
    monkeypatch.setattr("app.services.workspace_tools.invoke_workspace_tool", tool)
    assert await missing_create_inputs(SimpleNamespace(id="track"), object(), {}) == [
        ("team", "team")
    ]
    tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_stager_refuses_all_missing_inputs_before_minting(monkeypatch):
    track = SimpleNamespace(id="n.Track.example", workspace_id="ws")
    monkeypatch.setattr("app.models.nodes.Track.get", AsyncMock(return_value=track))
    monkeypatch.setattr(
        "app.services.policy_engine.evaluate",
        AsyncMock(return_value=SimpleNamespace(allowed=True)),
    )
    monkeypatch.setattr(
        bindings, "duplicate_create_block", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.agentive.staging_executors._resolve_entry_type_id",
        AsyncMock(return_value="n.EntryType.item"),
    )
    monkeypatch.setattr(
        "app.models.nodes.EntryType.get", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(
        "app.agentive.tooling.stagers_filing.missing_create_inputs",
        AsyncMock(return_value=[("team", "Team"), ("role", "Role")]),
    )
    principal = bindings._propose_principal.set("user")
    try:
        with pytest.raises(ValueError, match=r"Team.*Role.*Nothing was staged"):
            await bindings._stage_create_entry(
                {"track_id": track.id, "entry_type": "Item", "title": "Sample"}
            )
    finally:
        bindings._propose_principal.reset(principal)
