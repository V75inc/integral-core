"""Writes cannot race a track or App schema migration."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions import MigrationInProgressError
from app.services.migration_write_guard import assert_track_schema_writable


def _track() -> MagicMock:
    track = MagicMock()
    track.id = "track-1"
    track.nodes = AsyncMock(return_value=[])
    return track


@pytest.mark.asyncio
async def test_track_schema_write_is_blocked_during_migration():
    track = _track()
    profile = MagicMock()
    profile.id = "cp-1"
    profile.scope = "track"
    profile.migration_status = "in_progress"
    with patch(
        "app.services.migration_write_guard.get_track_attached_operational_model",
        new=AsyncMock(return_value=profile),
    ):
        with pytest.raises(MigrationInProgressError) as excinfo:
            await assert_track_schema_writable(track)

    assert excinfo.value.status_code == 409
    assert excinfo.value.details["blocking_migrations"] == [
        {
            "operational_model_id": "cp-1",
            "scope": "track",
            "migration_status": "in_progress",
        }
    ]


@pytest.mark.asyncio
async def test_track_schema_write_passes_when_no_migration_is_active():
    track = _track()
    profile = MagicMock()
    profile.migration_status = "complete"
    with patch(
        "app.services.migration_write_guard.get_track_attached_operational_model",
        new=AsyncMock(return_value=profile),
    ):
        await assert_track_schema_writable(track)
