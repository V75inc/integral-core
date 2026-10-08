"""Relation staging uses the canonical schema and retains existing many links."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive import staging_executors as se
from app.models.nodes import Entry, EntryType, Track


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "many,existing,target,expected",
    [
        (True, None, "a", ["a"]),
        (True, ["a"], "b", ["a", "b"]),
        (True, ["a"], "a", ["a"]),
        (False, "a", "b", "b"),
    ],
)
async def test_link_cardinality(monkeypatch, many, existing, target, expected):
    source = SimpleNamespace(
        type_id="type", track_id="track", custom_fields={"related": existing}
    )
    monkeypatch.setattr(Entry, "get", AsyncMock(return_value=source))
    monkeypatch.setattr(EntryType, "get", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(Track, "get", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(
        "app.services.operational_model_runtime.resolve_track_runtime_profile",
        AsyncMock(return_value=(None, {}, None)),
    )
    monkeypatch.setattr(
        "app.services.operational_model_entry_fields.resolve_entry_type_spec",
        lambda *_: {
            "fields": [
                {"key": "related", "type": "relation", "relation": {"many": many}}
            ]
        },
    )
    call = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(se, "_call_endpoint", call)
    await se._x_link_entries(
        "user",
        {"source_entry_id": "source", "field_key": "related", "target_id": target},
    )
    assert call.call_args.kwargs == {
        "entry_id": "source",
        "custom_fields": {"related": expected},
    }
    assert source.custom_fields["related"] == existing
