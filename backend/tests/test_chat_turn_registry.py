"""Tests for in-flight chat turn registry (I-CHAT-PAR-01)."""

from __future__ import annotations

import asyncio

import pytest

from app.api.errors import ResourceConflictError
from app.services import chat_turn_registry as registry


@pytest.fixture(autouse=True)
async def _clean_registry():
    await registry.reset_registry_for_tests()
    yield
    await registry.reset_registry_for_tests()


@pytest.mark.asyncio
async def test_acquire_and_release_turn() -> None:
    handle = await registry.acquire_turn(thread_id="t1", user_id="u1")
    assert handle.thread_id == "t1"
    await registry.release_turn("t1")
    assert registry.get_turn("t1") is None


@pytest.mark.asyncio
async def test_second_acquire_same_thread_raises_conflict() -> None:
    await registry.acquire_turn(thread_id="t1", user_id="u1")
    with pytest.raises(ResourceConflictError):
        await registry.acquire_turn(thread_id="t1", user_id="u1")


@pytest.mark.asyncio
async def test_parallel_acquire_different_threads() -> None:
    h1 = await registry.acquire_turn(thread_id="t1", user_id="u1")
    h2 = await registry.acquire_turn(thread_id="t2", user_id="u1")
    assert h1.thread_id == "t1"
    assert h2.thread_id == "t2"


@pytest.mark.asyncio
async def test_cancel_sets_event_and_invokes_hook() -> None:
    handle = await registry.acquire_turn(thread_id="t1", user_id="u1")
    called = asyncio.Event()

    def hook() -> None:
        called.set()

    handle.register_cancel_hook(hook)
    assert await registry.cancel_turn("t1") is True
    assert handle.cancel_event.is_set()
    assert called.is_set()


@pytest.mark.asyncio
async def test_acquire_records_origin() -> None:
    handle = await registry.acquire_turn(
        thread_id="t1", user_id="u1", origin="routine_task"
    )
    assert handle.origin == "routine_task"


@pytest.mark.asyncio
async def test_cancel_turn_if_origin_matches() -> None:
    handle = await registry.acquire_turn(
        thread_id="t1", user_id="u1", origin="routine_task"
    )
    assert await registry.cancel_turn_if_origin("t1", "routine_task") is True
    assert handle.cancel_event.is_set()


@pytest.mark.asyncio
async def test_cancel_turn_if_origin_miss_leaves_turn() -> None:
    handle = await registry.acquire_turn(thread_id="t1", user_id="u1", origin="")
    assert await registry.cancel_turn_if_origin("t1", "routine_task") is False
    assert not handle.cancel_event.is_set()
    assert registry.get_turn("t1") is handle


@pytest.mark.asyncio
async def test_cancel_turn_if_origin_no_handle() -> None:
    assert await registry.cancel_turn_if_origin("missing", "routine_task") is False
