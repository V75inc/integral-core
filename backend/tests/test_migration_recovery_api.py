"""Authorization and state gates for migration recovery endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions import BadRequestError


@pytest.mark.asyncio
async def test_retry_refuses_completed_migration():
    from app.api import operational_models as cp_api

    cp = MagicMock()
    cp.id = "cp-1"
    cp.migration_status = "complete"
    with (
        patch.object(cp_api, "resolve_principal_id", return_value="user-1"),
        patch("app.models.nodes.OperationalModel.get", new=AsyncMock(return_value=cp)),
        patch.object(
            cp_api, "_resolve_cp_edit_permission", new=AsyncMock(return_value=True)
        ),
    ):
        with pytest.raises(BadRequestError) as excinfo:
            await cp_api.retry_operational_model_migration(
                request=MagicMock(), operational_model_id="cp-1"
            )

    assert excinfo.value.details == {"migration_status": "complete"}


@pytest.mark.asyncio
async def test_retry_starts_the_single_runner_for_failed_migration():
    from app.api import operational_models as cp_api

    cp = MagicMock()
    cp.id = "cp-1"
    cp.migration_status = "failed"
    cp.manifest = {
        "operational_model_schema_version": 2,
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
        patch("app.models.nodes.OperationalModel.get", new=AsyncMock(return_value=cp)),
        patch.object(
            cp_api, "_resolve_cp_edit_permission", new=AsyncMock(return_value=True)
        ),
        patch(
            "app.services.migrations.runner.run_migration_async",
            new=AsyncMock(
                return_value={"status": "running", "affected_entry_count": 2}
            ),
        ) as runner,
        patch("app.agentive.work_models.WorkItem.find", new=AsyncMock(return_value=[])),
    ):
        result = await cp_api.retry_operational_model_migration(
            request=MagicMock(), operational_model_id="cp-1"
        )

    assert result["retried"] is True
    assert result["migration_tracker"]["executed"] is True
    assert runner.await_args.kwargs["retry_of_work_item_id"] == ""


@pytest.mark.asyncio
async def test_retry_creates_a_child_attempt_of_the_failed_durable_work():
    from app.api import operational_models as cp_api
    from app.services.migrations.runner import _manifest_fingerprint

    cp = MagicMock()
    cp.id = "cp-1"
    cp.migration_status = "failed"
    cp.manifest = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "track": {"entry_types": []},
        "migrations": [
            {
                "from_version": "1",
                "to_version": "2",
                "ops": [
                    {
                        "op": "rename_field",
                        "entry_type": "task",
                        "from": "a",
                        "to": "b",
                    }
                ],
            }
        ],
    }
    failed = MagicMock()
    failed.work_item_id = "migration-attempt-1"
    failed.status = "failed"
    failed.updated_at = "2026-09-22T12:00:00Z"
    failed.input_payload = {
        "operational_model_id": "cp-1",
        "manifest_fingerprint": _manifest_fingerprint(
            cp_api.compile_canonical_manifest(manifest=cp.manifest)
        ),
    }
    with (
        patch.object(cp_api, "resolve_principal_id", return_value="user-1"),
        patch("app.models.nodes.OperationalModel.get", new=AsyncMock(return_value=cp)),
        patch.object(
            cp_api, "_resolve_cp_edit_permission", new=AsyncMock(return_value=True)
        ),
        patch(
            "app.agentive.work_models.WorkItem.find",
            new=AsyncMock(return_value=[failed]),
        ),
        patch(
            "app.services.migrations.runner.run_migration_async",
            new=AsyncMock(return_value={"status": "queued"}),
        ) as runner,
    ):
        await cp_api.retry_operational_model_migration(
            request=MagicMock(), operational_model_id="cp-1"
        )

    assert runner.await_args.kwargs["retry_of_work_item_id"] == "migration-attempt-1"
