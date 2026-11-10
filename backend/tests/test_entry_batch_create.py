"""Batch creation preserves the current admission and per-item moderation contract."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, create_autospec

import pytest

from app.api.errors import BadRequestError, InsufficientPermissionsError
from app.services import entry_create, entry_effect_scope, entry_write_scope

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest.fixture
def batch_runtime(monkeypatch):
    graph = SimpleNamespace(database=object())
    track = SimpleNamespace(id="track", workspace_id="workspace")
    admitted = []
    returned = []

    @asynccontextmanager
    async def write_scope(_key):
        yield graph

    @asynccontextmanager
    async def staging(**kwargs):
        yield None

    @asynccontextmanager
    async def admit(**kwargs):
        admitted.append(kwargs)
        yield "admitted-user"

    async def write(**kwargs):
        if kwargs["moderation_error"] is not None:
            raise kwargs["moderation_error"]
        result = SimpleNamespace(
            id=f"entry-{len(returned)}",
            title=kwargs["title"],
            set_context=AsyncMock(),
        )
        returned.append(result)
        return result

    writer = create_autospec(entry_create._create_entry_in_track, side_effect=write)
    admission = create_autospec(
        entry_effect_scope.entry_effect_scope, side_effect=admit
    )
    monkeypatch.setattr("jvspatial.core.context.get_default_context", lambda: graph)
    monkeypatch.setattr(entry_write_scope, "entry_write_scope", write_scope)
    monkeypatch.setattr(
        entry_write_scope, "is_postgres_entry_transaction", lambda _: False
    )
    monkeypatch.setattr(
        "app.agentive.services.staging_execution.staged_entry_effect", staging
    )
    monkeypatch.setattr(
        "app.services.approved_effect.approved_capture_active",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(entry_create.Track, "get", AsyncMock(return_value=track))
    monkeypatch.setattr(entry_effect_scope, "entry_effect_scope", admission)
    monkeypatch.setattr(entry_create, "_create_entry_in_track", writer)
    return track, writer, admitted, returned, graph


async def test_batch_uses_current_admission_and_write_signatures(batch_runtime):
    track, writer, admitted, returned, graph = batch_runtime
    result = await entry_create.create_entries_in_track(
        track=track,
        user_id="caller",
        type_id="type",
        items=[{"title": "First"}, {"title": "Second", "custom_fields": {"code": 2}}],
    )
    assert result == returned and [item.title for item in result] == ["First", "Second"]
    assert len(admitted) == 1
    assert admitted[0]["payload"]["type_id"] == "type"
    assert admitted[0]["payload"]["custom_fields"] == {"code": 2}
    assert writer.await_count == 2
    assert all(
        call.kwargs["user_id"] == "admitted-user" for call in writer.await_args_list
    )
    for item in result:
        item.set_context.assert_awaited_once_with(graph)


@pytest.mark.parametrize("field", ["title", "body"])
async def test_batch_keeps_each_items_moderation_verdict(batch_runtime, field):
    track, writer, admitted, _, _ = batch_runtime
    with pytest.raises(BadRequestError):
        await entry_create.create_entries_in_track(
            track=track,
            user_id="caller",
            items=[{"title": "First"}, {field: "fucking"}],
        )
    assert len(admitted) == 1
    assert writer.await_count == 2
    assert writer.await_args_list[0].kwargs["moderation_error"] is None
    assert isinstance(writer.await_args.kwargs["moderation_error"], BadRequestError)


async def test_batch_retains_explicit_moderation_bypass(batch_runtime):
    track, writer, _, _, _ = batch_runtime
    result = await entry_create.create_entries_in_track(
        track=track,
        user_id="caller",
        items=[{"title": "fucking"}],
        skip_profanity=True,
    )
    assert len(result) == 1
    assert writer.await_args.kwargs["moderation_error"] is None


async def test_batch_refuses_unauthorized_content_before_reporting_moderation(
    batch_runtime, monkeypatch
):
    track, writer, _, _, _ = batch_runtime

    @asynccontextmanager
    async def denied(**kwargs):
        raise InsufficientPermissionsError(
            message="Current Entry effect authority denied"
        )
        yield  # pragma: no cover - admission refuses before entering the scope

    monkeypatch.setattr(entry_effect_scope, "entry_effect_scope", denied)
    with pytest.raises(InsufficientPermissionsError):
        await entry_create.create_entries_in_track(
            track=track, user_id="caller", items=[{"title": "fucking"}]
        )
    writer.assert_not_awaited()
