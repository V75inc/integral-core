"""Tests for entry/track staging labels."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive.tooling import bindings


@pytest.mark.asyncio
async def test_stage_update_entry_uses_entry_title(monkeypatch):
    entry_id = "n.Entry.abc123def456"
    monkeypatch.setattr(
        bindings._sd,
        "load_entry_record",
        AsyncMock(
            return_value={
                "id": entry_id,
                "title": "Q2 Planning",
                "body": "Body",
                "status": "open",
                "record_revision": 4,
                "schema_revision": 7,
            }
        ),
    )

    staged = await bindings._stage_update_entry(
        {
            "entry_id": entry_id,
            "updates": {"title": "Q2 Plan (revised)", "status": "done"},
        }
    )

    assert staged["summary"] == "Update entry “Q2 Planning”"
    assert "**Update entry** *Q2 Planning*" in staged["diff_human"]
    assert entry_id not in staged["summary"]
    assert "`title`" in staged["diff_human"] or "**title:**" in staged["diff_human"]
    assert "Q2 Plan (revised)" in staged["diff_human"]
    assert staged["payload"]["expected_record_revision"] == 4
    assert staged["payload"]["expected_schema_revision"] == 7


@pytest.mark.asyncio
async def test_stage_update_entry_prefers_existing_profile_status(monkeypatch):
    """A business Status field must not silently mutate platform lifecycle."""
    entry_id = "n.Entry.abc123def456"
    token = bindings._propose_principal.set("u1")
    monkeypatch.setattr(
        bindings._sd,
        "load_entry_record",
        AsyncMock(
            return_value={
                "id": entry_id,
                "title": "Fleet 42",
                "status": "active",
                "custom_fields": {"status": "Available"},
            }
        ),
    )

    try:
        staged = await bindings._stage_update_entry(
            {"entry_id": entry_id, "updates": {"status": "Maintenance"}}
        )
    finally:
        bindings._propose_principal.reset(token)

    assert "status" not in staged["payload"]
    assert staged["payload"]["fields"] == {"status": "Maintenance"}


def _row(title, fields=None, status="active", entry_id="n.Entry.existing"):
    return SimpleNamespace(
        id=entry_id,
        title=title,
        custom_fields=fields or {},
        status=status,
    )


def test_a_stated_date_is_not_a_bare_month():
    assert bindings.text_has_calendar_date("1 October")
    assert bindings.text_has_calendar_date("October 4th")
    assert bindings.text_has_calendar_date("2026-10-01")
    assert not bindings.text_has_calendar_date("Sandy Singh Appointment")
    missing = bindings.missing_date_fields(
        ["date"], "Set it to 1 October", {}, {"date": None}
    )
    assert missing == ["date"]
    assert (
        bindings.missing_date_fields(
            ["date"], "Set it to 1 October", {"date": "2026-10-01"}, {"date": None}
        )
        == []
    )
    assert "updates.fields.date" in bindings.date_field_refusal(["date"], update=True)


def test_two_name_matches_are_both_named():
    rows = [
        _row("Sandy Singh Appointment", entry_id="n.Entry.oct1"),
        _row("Sandy Singh - Appointment on October 4th", entry_id="n.Entry.oct4"),
    ]
    found = bindings.find_likely_duplicates(rows, "Follow-up with Sandy Singh")
    assert [row.id for row in found] == ["n.Entry.oct1", "n.Entry.oct4"]


def test_likely_duplicate_matches_a_shared_name_under_a_different_title():
    rows = [_row("Sandy Singh — 4 October", entry_id="n.Entry.oct4")]
    match = bindings.find_likely_duplicate(rows, "Sandy Singh Appointment")
    assert match.id == "n.Entry.oct4"
    assert bindings.find_likely_duplicate(rows, "Appointment on 1 October") is None
    assert (
        bindings.find_likely_duplicate(
            [_row("Team standup")], "Sandy Singh Appointment"
        )
        is None
    )
    assert (
        bindings.find_likely_duplicate(
            [_row("Sandy Singh", status="deleted")], "Sandy Singh Appointment"
        )
        is None
    )


@pytest.mark.asyncio
async def test_explicit_separate_create_is_still_a_proposal_not_a_lexical_gate(
    monkeypatch,
):
    lookup = AsyncMock(return_value=_row("Same title"))
    monkeypatch.setattr(bindings, "_find_visible_entry_with_title", lookup)
    principal = bindings._propose_principal.set("u1")
    try:
        assert (
            await bindings.duplicate_create_block(
                track_id="track-a", title="Same title", allow_duplicate_title=True
            )
            is None
        )
        lookup.assert_not_awaited()
        assert "already exists" in await bindings.duplicate_create_block(
            track_id="track-a", title="Same title"
        )
    finally:
        bindings._propose_principal.reset(principal)


@pytest.mark.asyncio
async def test_stage_create_entry_refuses_an_existing_named_record(monkeypatch):
    monkeypatch.setattr(
        "app.services.view_create_resolution.load_entry_types_for_track",
        AsyncMock(return_value=[]),
    )

    """An update request must not surface a duplicate create approval."""
    from app.models.nodes import Track
    from app.services import policy_engine

    token = bindings._propose_principal.set("u1")
    monkeypatch.setattr(
        Track, "get", AsyncMock(return_value=SimpleNamespace(workspace_id="ws-test"))
    )
    monkeypatch.setattr(
        policy_engine,
        "evaluate",
        AsyncMock(return_value=SimpleNamespace(allowed=True)),
    )
    monkeypatch.setattr(
        bindings,
        "_find_visible_entry_with_title",
        AsyncMock(return_value=SimpleNamespace(id="n.Entry.existing")),
    )
    try:
        with pytest.raises(ValueError, match="already exists.*update_entry"):
            await bindings._stage_create_entry(
                {
                    "track_id": "n.Track.cars",
                    "title": "Toyota Camry",
                    "entry_type": "Car",
                    "fields": {"status": "Available"},
                }
            )
    finally:
        bindings._propose_principal.reset(token)


@pytest.mark.asyncio
async def test_shared_prose_and_approximate_titles_do_not_establish_identity(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.permissions.get_user_accessible_entries",
        AsyncMock(
            return_value=[
                _row(
                    "Sandy Singh — 4 October", {"description": "Follow-up appointment"}
                )
            ]
        ),
    )
    principal = bindings._propose_principal.set("u1")
    try:
        assert (
            await bindings.duplicate_create_block(
                track_id="track-a",
                title="Sandy Singh — 5 October",
                text="Follow-up appointment",
            )
            is None
        )
    finally:
        bindings._propose_principal.reset(principal)


@pytest.mark.asyncio
async def test_stage_delete_entry_uses_entry_title(monkeypatch):
    entry_id = "n.Entry.abc123def456"
    monkeypatch.setattr(
        bindings._sd,
        "load_entry_record",
        AsyncMock(return_value={"id": entry_id, "title": "Old draft"}),
    )

    staged = await bindings._stage_delete_entry({"entry_id": entry_id})

    assert staged["summary"] == "Delete entry “Old draft”"
    assert "**Delete entry** *Old draft*" in staged["diff_human"]
    assert entry_id not in staged["summary"]
    assert "Permanently deletes" in staged["diff_human"]
    assert "cannot be undone" in staged["diff_human"]
    assert "Reversible" not in staged["diff_human"]
    assert "Soft-deletes" not in staged["diff_human"]


@pytest.mark.asyncio
async def test_stage_update_track_uses_track_title(monkeypatch):
    track_id = "n.Track.abc123def456"
    monkeypatch.setattr(
        bindings._sd,
        "load_track_record",
        AsyncMock(
            return_value={
                "id": track_id,
                "title": "Marketing",
                "visibility": "private",
            }
        ),
    )

    staged = await bindings._stage_update_track(
        {
            "track_id": track_id,
            "updates": {"title": "Marketing (2026)", "visibility": "org"},
        }
    )

    assert staged["summary"] == "Update track “Marketing”"
    assert "**Update track** *Marketing*" in staged["diff_human"]
    assert track_id not in staged["summary"]
    assert "Marketing (2026)" in staged["diff_human"]


@pytest.mark.asyncio
async def test_stage_delete_track_uses_track_title(monkeypatch):
    track_id = "n.Track.abc123def456"
    monkeypatch.setattr(
        bindings._sd,
        "load_track_record",
        AsyncMock(return_value={"id": track_id, "title": "Archive", "entry_count": 3}),
    )

    staged = await bindings._stage_delete_track({"track_id": track_id})

    assert staged["summary"] == "Delete track “Archive”"
    assert "**Delete track** *Archive*" in staged["diff_human"]
    assert "**3** entries" in staged["diff_human"]
    assert track_id not in staged["summary"]


@pytest.mark.asyncio
async def test_stage_update_entry_refuses_missing_id(monkeypatch):
    monkeypatch.setattr(bindings._sd, "load_entry_record", AsyncMock(return_value=None))
    entry_id = "n.Entry.david-appointment-next-month"

    with pytest.raises(ValueError, match="Nothing was staged") as exc:
        await bindings._stage_update_entry(
            {"entry_id": entry_id, "updates": {"fields": {"Date": "2026-10-10"}}}
        )

    assert entry_id in str(exc.value)
    assert "integral_query_entries" in str(exc.value)


@pytest.mark.asyncio
async def test_stage_delete_entry_refuses_missing_id(monkeypatch):
    monkeypatch.setattr(bindings._sd, "load_entry_record", AsyncMock(return_value=None))

    with pytest.raises(ValueError, match="Nothing was staged"):
        await bindings._stage_delete_entry(
            {"entry_id": "n.Entry.david-appointment-next-month"}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("hint", ["type-book", "book", "Book"])
async def test_duplicate_identity_is_verified_by_destination_type_catalogue(
    monkeypatch, hint
):
    from app.models.nodes import Track

    types = [
        SimpleNamespace(id="type-book", name="Book", key="book", form_schema={}),
        SimpleNamespace(id="type-note", name="Note", key="note", form_schema={}),
    ]
    note = _row("Shared title")
    note.type_id = "type-note"
    book = _row("Different book", {"description": "Shared title"})
    book.type_id = "type-book"
    monkeypatch.setattr(
        Track, "get", AsyncMock(return_value=SimpleNamespace(id="track-a"))
    )
    monkeypatch.setattr(
        "app.services.view_create_resolution.load_entry_types_for_track",
        AsyncMock(return_value=types),
    )
    monkeypatch.setattr(
        "app.services.permissions.get_user_accessible_entries",
        AsyncMock(return_value=[note, book]),
    )
    principal = bindings._propose_principal.set("u1")
    try:
        assert (
            await bindings.duplicate_create_block(
                track_id="track-a",
                title="Shared title",
                entry_type=hint,
                text="Different book Shared title",
            )
            is None
        )
        assert "already exists" in await bindings.duplicate_create_block(
            track_id="track-a", title="Different book", entry_type=hint
        )
        legacy = _row("Shared title")
        legacy.type_id = "legacy-unknown"
        monkeypatch.setattr(
            "app.services.permissions.get_user_accessible_entries",
            AsyncMock(return_value=[legacy]),
        )
        assert "already exists" in await bindings.duplicate_create_block(
            track_id="track-a", title="Shared title", entry_type=hint
        )
    finally:
        bindings._propose_principal.reset(principal)


@pytest.mark.asyncio
async def test_unverified_type_hint_cannot_bypass_duplicate_identity(monkeypatch):
    from app.models.nodes import Track

    note = _row("Same title")
    note.type_id = "type-note"
    monkeypatch.setattr(
        Track, "get", AsyncMock(return_value=SimpleNamespace(id="track-a"))
    )
    monkeypatch.setattr(
        "app.services.view_create_resolution.load_entry_types_for_track",
        AsyncMock(
            return_value=[
                SimpleNamespace(id="type-note", name="Note", key="note", form_schema={})
            ]
        ),
    )
    monkeypatch.setattr(
        "app.services.permissions.get_user_accessible_entries",
        AsyncMock(return_value=[note]),
    )
    principal = bindings._propose_principal.set("u1")
    try:
        assert "already exists" in await bindings.duplicate_create_block(
            track_id="track-a", title="Same title", entry_type="unverified type"
        )
    finally:
        bindings._propose_principal.reset(principal)
