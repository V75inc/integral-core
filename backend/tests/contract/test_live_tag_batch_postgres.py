"""Live taxonomy batches preserve prior tags and recover committed steps."""

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive import staging
from app.agentive.services import staging_apply, staging_command_batch
from app.agentive.staging import create_staged_change
from app.models.nodes import Tag
from app.services.agent_scope import current_scope_workspace_id
from app.services.resource_location import locate_resource
from app.services.tag_service import create_tag_for_scope
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.mark.parametrize("owner_kind", ["track", "app"])
async def test_live_tag_batch_readback_and_retry_preserve_existing_tags(
    postgres_raw_db, monkeypatch, owner_kind
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "33" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, track, _ = await proposal()
        location = await locate_resource("track", track.id)
        assert location is not None
        owner_id = track.id if owner_kind == "track" else location.app_id
        assert owner_id
        owner = {f"{owner_kind}_id": owner_id}
        tag_query = {f"context.{owner_kind}_id": owner_id}
        existing = await create_tag_for_scope(
            user_id=auth.id,
            **owner,
            name="Existing",
            name_fold="existing",
            color="#123456",
        )
        reset = current_scope_workspace_id.set(workspace.id)
        expected = {"Alpha": "#112233", "Beta": "#445566", "Gamma": "#778899"}
        try:
            change = await create_staged_change(
                user_id=auth.id,
                session_id=None,
                kind="batch",
                summary="Add live tags",
                diff_human="Add three tags and preserve existing ones",
                diff_machine={},
                payload={
                    "operations": [
                        {
                            "kind": "create_tag",
                            "payload": {
                                **owner,
                                "name": name,
                                "color": color,
                            },
                        }
                        for name, color in expected.items()
                    ]
                },
            )
        finally:
            current_scope_workspace_id.reset(reset)
        original = staging_command_batch.apply_staged_command_once
        interrupted = False

        async def lose_worker(**kwargs):
            nonlocal interrupted
            result = await original(**kwargs)
            if not interrupted:
                interrupted = True
                raise RuntimeError("Lost completion after first tag")
            return result

        monkeypatch.setattr(
            staging_command_batch, "apply_staged_command_once", lose_worker
        )
        with pytest.raises(RuntimeError, match="Lost completion"):
            await staging_apply.bless_and_execute(user_id=auth.id, token=change.token)
        first_tags = await Tag.find(tag_query)
        first_id = next(tag.id for tag in first_tags if tag.name == "Alpha")
        staging._tokens.clear()
        monkeypatch.setattr(
            staging_command_batch, "apply_staged_command_once", original
        )
        result = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert result["consumed"] is True, result
        recovered_tag = result["execute_result"]["results"][0]["result"]["tag"]
        assert recovered_tag["id"] == first_id
        assert recovered_tag["color"] == expected["Alpha"]
        tags = await Tag.find(tag_query)
        assert len(tags) == 4
        assert {tag.name: tag.color for tag in tags} == {
            "Existing": "#123456",
            **expected,
        }
        assert next(tag.id for tag in tags if tag.name == "Existing") == existing.id
        assert next(tag.id for tag in tags if tag.name == "Alpha") == first_id
