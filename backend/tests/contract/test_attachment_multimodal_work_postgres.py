"""File transforms use the real leased model boundary without corpus prompts."""

import hashlib
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async
from PIL import Image

from app.agentive.harness import litellm_model
from app.agentive.harness.contracts import ResolvedModelRoute
from app.agentive.harness.model_observations import list_run_model_request_observations
from app.agentive.services import work_items, work_worker
from app.agentive.services.work_execution import deterministic_run_id
from app.config import settings
from app.models.edges import HAS_ATTACHMENT
from app.models.nodes import Attachment
from app.schemas.agentive.work import WorkError
from app.services.attachment_metadata import analysis
from tests.test_resource_location import graph

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
async def transactional_graph(postgres_raw_db):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        yield


@pytest.mark.parametrize(
    "late_change",
    [None, "native-reextract", "native-failure", "replacement", "cancel", "disable"],
)
async def test_multimodal_dispatch_is_bounded_and_late_changes_withhold_publication(
    monkeypatch, late_change
):
    monkeypatch.setenv("LITELLM_MODE", "PRODUCTION")
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", "/tmp/integral-litellm-test-cache")
    monkeypatch.setenv("EMBEDDING_STORE_DRIVER", "auto")
    monkeypatch.setattr(
        "app.services.credential_crypto._resolve_key", lambda: (b"k" * 32, None)
    )
    owner, workspace, _, _, entry = await graph()
    entry.body = "Private corpus sentinel must never enter the image prompt."
    await entry.save()
    stream = io.BytesIO()
    Image.new("RGB", (16, 16), "blue").save(stream, format="PNG")
    blob = stream.getvalue()
    attachment = await Attachment.create(
        filename="concept.png",
        mime_type="image/png",
        size=len(blob),
        storage_key="fixture-only",
        uploaded_by=owner.id,
        content_hash=hashlib.sha256(blob).hexdigest(),
        scan_status="clean",
        metadata_status="complete",
        metadata_extractor_version=(
            0
            if late_change in {"native-reextract", "native-failure"}
            else settings.ATTACHMENT_METADATA_EXTRACTOR_VERSION
        ),
    )
    await entry.connect(attachment, edge=HAS_ATTACHMENT)
    route = ResolvedModelRoute(
        provider="openai",
        model="openai/synthetic-vision",
        api_key="fixture-key",
        credential_source="workspace_byok",
        credential_ref="owned-fixture",
    )
    route_lookup = AsyncMock(return_value=route)
    monkeypatch.setattr(analysis, "_vision_route", route_lookup)
    monkeypatch.setattr(
        "app.services.attachment_storage.get_attachment_storage_service",
        lambda: SimpleNamespace(read_attachment=AsyncMock(return_value=blob)),
    )
    monkeypatch.setattr(
        "app.services.attachment_metadata.service.get_attachment_storage_service",
        lambda: SimpleNamespace(
            read_attachment=AsyncMock(
                return_value=None if late_change == "native-failure" else blob
            )
        ),
    )
    await analysis.enqueue_analysis(attachment)
    from jvspatial.core.context import get_default_context

    rows = await get_default_context().database.find(
        "object", {"entity": "WorkItem", "context.kind": "attachment_analysis"}, limit=2
    )
    assert len(rows) == 1
    identity = rows[0]["context"]["work_item_id"]
    claimed = await work_items.claim_due_candidate(
        worker_id="image-test", work_item_id=identity, lease_seconds=60
    )
    assert claimed is not None
    calls = []
    metering = AsyncMock()
    monkeypatch.setattr("app.services.host_hooks.record_usage_event", metering)

    async def complete(**kwargs):
        calls.append(kwargs)
        if late_change == "replacement":
            attachment.content_hash = "replacement"
            await attachment.save()
        elif late_change == "cancel":
            await work_items.cancel_work_item(identity)
        elif late_change == "disable":
            route_lookup.return_value = None
        name = kwargs["tools"][0]["function"]["name"]
        return {
            "id": "fixture-response",
            "object": "chat.completion",
            "created": 1,
            "model": "synthetic-vision",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "output",
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "arguments": json.dumps(
                                        {
                                            "description": "A blue square.",
                                            "visible_text": "",
                                            "keywords": ["blue"],
                                        }
                                    ),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        }

    build = litellm_model.build_litellm_sdk_model
    monkeypatch.setattr(
        litellm_model,
        "build_litellm_sdk_model",
        lambda **kwargs: build(**kwargs, completion=complete),
    )
    if late_change == "cancel":
        with pytest.raises(WorkError) as error:
            await work_worker.execute_claimed_work(
                claimed, worker_id="image-test", lease_seconds=60
            )
        assert error.value.code == "work.cancelled"
        done = await work_items._current_work_item(identity)
        assert done.cancel_requested_at
    else:
        done = await work_worker.execute_claimed_work(
            claimed, worker_id="image-test", lease_seconds=60
        )
    if late_change == "native-failure":
        assert not calls and done.status == "failed"
        metering.assert_not_awaited()
        assert (
            "semantic"
            not in (await Attachment.find_one({"id": attachment.id})).metadata
        )
        return
    assert len(calls) == 1
    assert "Private corpus sentinel" not in json.dumps(calls[0]["messages"])
    assert len(calls[0]["tools"]) == 1  # Structured output only; no execution tools.
    assert calls[0]["num_retries"] == 0
    assert metering.await_count == 1
    assert metering.await_args.kwargs["source"] == "byok"
    assert metering.await_args.kwargs["user_id"] == owner.id
    current = await Attachment.get(attachment.id)
    if late_change in {None, "native-reextract"}:
        assert done.status == "succeeded"
        assert current.metadata["semantic"]["description"] == "A blue square."
        assert current.metadata["semantic"]["source_hash"] == attachment.content_hash
        if late_change == "native-reextract":
            assert current.width == current.height == 16
    else:
        assert done.status != "succeeded"
        assert "semantic" not in current.metadata
    observations = await list_run_model_request_observations(
        run_id=deterministic_run_id(work_item_id=identity, attempt=claimed.attempt),
        principal_id=owner.id,
        workspace_id=workspace.id,
        thread_id=claimed.thread_id,
    )
    assert {row.outcome for row in observations} == {"dispatch_intent", "responded"}
