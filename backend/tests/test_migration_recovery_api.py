"""Authorization and state gates for migration recovery endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions import BadRequestError
from app.services.model_authority import get_current_model
from tests.test_model_api_authority import request_for
from tests.test_resource_location import graph


async def recovery_model(status="failed"):
    owner, workspace, _, track, _ = await graph()
    cp = await get_current_model(track.attached_operational_model_id)
    assert cp is not None
    cp.migration_status = status
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
    await cp.save()
    return owner, workspace, cp


@pytest.mark.asyncio
async def test_retry_refuses_completed_migration():
    from app.api import operational_models as cp_api

    owner, workspace, cp = await recovery_model("complete")
    with pytest.raises(BadRequestError) as excinfo:
        await cp_api.retry_operational_model_migration(
            request=request_for(owner.id, workspace.id), operational_model_id=cp.id
        )

    assert excinfo.value.details == {"migration_status": "complete"}


@pytest.mark.asyncio
async def test_retry_starts_the_single_runner_for_failed_migration():
    from app.api import operational_models as cp_api

    owner, workspace, cp = await recovery_model("failed")
    with (
        patch(
            "app.services.migrations.runner.run_migration_async",
            new=AsyncMock(
                return_value={"status": "running", "affected_entry_count": 2}
            ),
        ) as runner,
        patch("app.agentive.work_models.WorkItem.find", new=AsyncMock(return_value=[])),
    ):
        result = await cp_api.retry_operational_model_migration(
            request=request_for(owner.id, workspace.id), operational_model_id=cp.id
        )

    assert result["retried"] is True
    assert result["migration_tracker"]["executed"] is True
    assert runner.await_args.kwargs["retry_of_work_item_id"] == ""


@pytest.mark.asyncio
async def test_retry_creates_a_child_attempt_of_the_failed_durable_work():
    from app.api import operational_models as cp_api
    from app.services.migrations.runner import _manifest_fingerprint

    owner, workspace, cp = await recovery_model("failed")
    failed = MagicMock()
    failed.work_item_id = "migration-attempt-1"
    failed.status = "failed"
    failed.updated_at = "2026-09-22T12:00:00Z"
    failed.input_payload = {
        "operational_model_id": cp.id,
        "manifest_fingerprint": _manifest_fingerprint(
            cp_api.compile_canonical_manifest(manifest=cp.manifest)
        ),
    }
    with (
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
            request=request_for(owner.id, workspace.id), operational_model_id=cp.id
        )

    assert runner.await_args.kwargs["retry_of_work_item_id"] == "migration-attempt-1"


@pytest.mark.asyncio
async def test_retry_denied_after_current_membership_removal_without_enqueue():
    from app.api import operational_models as cp_api
    from app.api.errors import InsufficientPermissionsError
    from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF

    _, workspace, cp = await recovery_model()
    curator, _, _, _, _ = await graph()
    # The target's exact Track requires configuration authority in its workspace.
    from app.services.model_authority import resolve_model_authority

    authority = await resolve_model_authority(cp.id)
    target_track_id = authority.owners[0][1]
    from app.models.nodes import Track

    target = await Track.get(target_track_id)
    await curator.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    await curator.connect(target, edge=COLLABORATES_ON, role="admin")
    with patch(
        "app.services.migrations.runner.run_migration_async",
        new=AsyncMock(return_value={"status": "queued"}),
    ) as runner:
        await cp_api.retry_operational_model_migration(
            request_for(curator.id, workspace.id), cp.id
        )
        ctx = await curator.get_context()
        for edge in await ctx.find_edges_between(
            curator.id, workspace.id, edge_class=IS_MEMBER_OF
        ):
            await edge.delete()
        with pytest.raises(InsufficientPermissionsError):
            await cp_api.retry_operational_model_migration(
                request_for(curator.id, workspace.id), cp.id
            )
        assert runner.await_count == 1
