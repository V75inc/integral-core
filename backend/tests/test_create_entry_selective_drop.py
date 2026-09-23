"""create_entry executor: a single bad field must not discard all the others.

Regression for silent data loss — when one custom field fails validation (e.g.
a relation field handed a NAME instead of an id), the executor used to drop the
ENTIRE custom_fields block and still report success. It now peels off only the
offending field and retries with the rest, recording what was dropped.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.agentive import staging_executors


@pytest.mark.asyncio
async def test_selective_drop_keeps_valid_fields(monkeypatch):
    """Only the field named in the error is dropped; the rest survive."""
    seen_custom_fields: List[Any] = []

    async def fake_call_endpoint(handler, user_id, **body):
        cf = body.get("custom_fields")
        seen_custom_fields.append(cf)
        # Fail while the bad relation field is still present.
        if cf and "employee" in cf:
            return {
                "error": True,
                "status_code": 400,
                "error_code": "bad_request",
                "message": "Relation field 'employee' references unknown entry 'Founder'",
            }
        return {"entry": {"id": "n.Entry.OK", "custom_fields": cf or {}}}

    monkeypatch.setattr(staging_executors, "_call_endpoint", fake_call_endpoint)
    monkeypatch.setattr(staging_executors, "_resolve_entry_type_id", _async_none)

    result = await staging_executors._x_create_entry(
        "u1",
        {
            "track_id": "t1",
            "title": "Vacation - Founder",
            "entry_type": "time_off_request",
            "fields": {
                "employee": "Founder",
                "days": 3,
                "status": "approved",
                "start_date": "2026-06-25",
            },
        },
    )

    assert not result.get("error")
    assert result.get("fields_dropped") == ["employee"]
    # The successful retry carried the surviving fields (employee removed).
    final = seen_custom_fields[-1]
    assert final == {"days": 3, "status": "approved", "start_date": "2026-06-25"}


@pytest.mark.asyncio
async def test_unlocalizable_error_falls_back_to_drop_all(monkeypatch):
    """When the error names no field, fall back to dropping all custom_fields."""

    async def fake_call_endpoint(handler, user_id, **body):
        if body.get("custom_fields"):
            return {
                "error": True,
                "status_code": 422,
                "error_code": "unprocessable",
                "message": "schema rejected the payload",  # no 'Field X'
            }
        return {"entry": {"id": "n.Entry.OK", "custom_fields": {}}}

    monkeypatch.setattr(staging_executors, "_call_endpoint", fake_call_endpoint)
    monkeypatch.setattr(staging_executors, "_resolve_entry_type_id", _async_none)

    result = await staging_executors._x_create_entry(
        "u1",
        {"track_id": "t1", "title": "x", "fields": {"a": 1, "b": 2}},
    )
    assert not result.get("error")
    assert set(result.get("fields_dropped") or []) == {"a", "b"}


@pytest.mark.asyncio
async def test_approved_scaffold_strict_fields_fail_without_dropping(monkeypatch):
    calls = []

    async def fake_call_endpoint(handler, user_id, **body):
        calls.append(body)
        return {
            "error": True,
            "status_code": 400,
            "message": "Field 'vehicle' is invalid",
        }

    monkeypatch.setattr(staging_executors, "_call_endpoint", fake_call_endpoint)
    result = await staging_executors._x_create_entry(
        "u1",
        {
            "track_id": "t1",
            "title": "Rental",
            "fields": {"vehicle": "n.Entry.1"},
            "strict_fields": True,
        },
    )
    assert result["error"] is True
    assert len(calls) == 1
    assert calls[0]["custom_fields"] == {"vehicle": "n.Entry.1"}


async def _async_none(*args, **kwargs):
    return None


def test_entry_type_key_normalization():
    """Name and key both normalize to the same slug-key."""
    from app.agentive.staging_executors import _entry_type_key

    assert _entry_type_key("Time-off request") == "time_off_request"
    assert _entry_type_key("time_off_request") == "time_off_request"
    assert _entry_type_key("Opportunity") == "opportunity"
    assert _entry_type_key("Time-off request") == _entry_type_key("time_off_request")


@pytest.mark.asyncio
async def test_resolve_entry_type_matches_by_key(monkeypatch):
    """A key ('time_off_request') resolves the EntryType named 'Time-off request'."""
    from app.agentive import staging_executors as sx

    class _ET:
        def __init__(self, _id, name):
            self.id, self.name = _id, name

    class _CP:
        async def nodes(self, **kwargs):
            return [
                _ET("n.EntryType.TOR", "Time-off request"),
                _ET("n.EntryType.X", "Post"),
            ]

    async def fake_track_get(tid):
        return object()

    async def fake_resolve_runtime(track):
        return _CP(), None, None

    monkeypatch.setattr("app.models.nodes.Track.get", staticmethod(fake_track_get))
    monkeypatch.setattr(
        "app.services.operational_model_runtime.resolve_track_runtime_profile",
        fake_resolve_runtime,
    )

    got = await sx._resolve_entry_type_id("t1", "time_off_request")
    assert got == "n.EntryType.TOR"
