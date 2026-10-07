"""A rejected create never retries with fewer fields or claims success."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.agentive import staging_executors


@pytest.mark.asyncio
async def test_rejected_relation_field_does_not_retry_without_it(monkeypatch):
    """A failed relation means the whole approved write was not applied."""
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

    async def resolved_type(*_args):
        return "n.EntryType.request"

    monkeypatch.setattr(staging_executors, "_resolve_entry_type_id", resolved_type)

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

    assert result["error"] is True
    assert result["fields_not_applied"] == [
        "employee",
        "days",
        "status",
        "start_date",
    ]
    assert len(seen_custom_fields) == 1
    assert seen_custom_fields[0] == {
        "employee": "Founder",
        "days": 3,
        "status": "approved",
        "start_date": "2026-06-25",
    }


@pytest.mark.asyncio
async def test_unlocalizable_error_reports_all_fields_not_applied(monkeypatch):
    """A schema refusal cannot be disguised as a fieldless create."""
    calls = []

    async def fake_call_endpoint(handler, user_id, **body):
        calls.append(body)
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
    assert result["error"] is True
    assert result["fields_not_applied"] == ["a", "b"]
    assert len(calls) == 1


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


@pytest.mark.asyncio
async def test_explicit_unresolved_type_never_creates_using_default(monkeypatch):
    from unittest.mock import AsyncMock

    endpoint = AsyncMock()
    monkeypatch.setattr(staging_executors, "_call_endpoint", endpoint)
    monkeypatch.setattr(staging_executors, "_resolve_entry_type_id", _async_none)
    result = await staging_executors._x_create_entry(
        "u1", {"track_id": "t1", "title": "Document", "entry_type": "artifact"}
    )
    assert result["error_code"] == "entry_type_unresolved"
    endpoint.assert_not_awaited()


@pytest.mark.asyncio
async def test_manifest_type_key_resolves_when_display_name_is_different(monkeypatch):
    from types import SimpleNamespace

    from app.agentive import staging_executors as sx

    class Profile:
        async def nodes(self, **kwargs):
            return [
                SimpleNamespace(id="type-default", name="Task", form_schema={}),
                SimpleNamespace(
                    id="type-artifact",
                    name="Supporting document",
                    form_schema={"_manifest_entry_type_key": "artifact"},
                ),
            ]

    async def profile(_track):
        return Profile(), {}, None

    async def track(_id):
        return object()

    monkeypatch.setattr("app.models.nodes.Track.get", track)
    monkeypatch.setattr(
        "app.services.operational_model_runtime.resolve_track_runtime_profile", profile
    )
    assert await sx._resolve_entry_type_id("track", "artifact") == "type-artifact"
