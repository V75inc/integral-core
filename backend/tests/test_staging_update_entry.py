"""Tests for entry/track staging labels."""

from __future__ import annotations

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
