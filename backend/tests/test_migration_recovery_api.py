"""Authorization and state gates for migration recovery endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions import BadRequestError


@pytest.mark.asyncio
async def test_retry_refuses_completed_migration():
    from app.api import content_profiles as cp_api

    cp = MagicMock()
    cp.id = "cp-1"
    cp.migration_status = "complete"
    with (
        patch.object(cp_api, "resolve_principal_id", return_value="user-1"),
        patch("app.models.nodes.ContentProfile.get", new=AsyncMock(return_value=cp)),
        patch.object(
            cp_api, "_resolve_cp_edit_permission", new=AsyncMock(return_value=True)
        ),
    ):
        with pytest.raises(BadRequestError) as excinfo:
            await cp_api.retry_content_profile_migration(
                request=MagicMock(), content_profile_id="cp-1"
            )

    assert excinfo.value.details == {"migration_status": "complete"}


@pytest.mark.asyncio
async def test_retry_starts_the_single_runner_for_failed_migration():
    from app.api import content_profiles as cp_api

    cp = MagicMock()
    cp.id = "cp-1"
    cp.migration_status = "failed"
    cp.manifest = {
        "content_profile_schema_version": 2,
        "scope": "track",
        "track": {"entry_types": []},
        "migrations": [
            {
                "from_version": "1",
                "to_version": "2",
                "ops": [
                    {"op": "rename_field", "entry_type": "task", "from": "a", "to": "b"}
                ],
            }
        ],
    }
    with (
        patch.object(cp_api, "resolve_principal_id", return_value="user-1"),
        patch("app.models.nodes.ContentProfile.get", new=AsyncMock(return_value=cp)),
        patch.object(
            cp_api, "_resolve_cp_edit_permission", new=AsyncMock(return_value=True)
        ),
        patch(
            "app.services.migrations.runner.run_migration_async",
            new=AsyncMock(
                return_value={"status": "running", "affected_entry_count": 2}
            ),
        ) as runner,
    ):
        result = await cp_api.retry_content_profile_migration(
            request=MagicMock(), content_profile_id="cp-1"
        )

    assert result["retried"] is True
    assert result["migration_tracker"]["executed"] is True
    runner.assert_awaited_once()
