"""Local fallback locks serialize active commands and release idle identities."""

import asyncio
from types import SimpleNamespace

import pytest

from app.services import entry_write_scope as module


@pytest.fixture
def local_context(monkeypatch):
    database = type("LocalStore", (), {"__module__": "jvspatial.memory"})()
    context = SimpleNamespace(database=database)
    monkeypatch.setattr(module, "get_default_context", lambda: context)
    return context


@pytest.mark.asyncio
async def test_idle_local_entry_locks_do_not_accumulate(local_context):
    for index in range(100):
        async with module.entry_write_scope(f"entry-{index}") as context:
            assert context is local_context
            assert len(module._local_locks) == 1
        assert len(module._local_locks) == 0


@pytest.mark.asyncio
async def test_local_waiter_keeps_shared_lock_until_both_commands_finish(local_context):
    first_entered = asyncio.Event()
    release = asyncio.Event()
    second_entered = asyncio.Event()

    async def first():
        async with module.entry_write_scope("same-entry"):
            first_entered.set()
            await release.wait()

    async def second():
        async with module.entry_write_scope("same-entry"):
            second_entered.set()

    owner = asyncio.create_task(first())
    await first_entered.wait()
    waiter = asyncio.create_task(second())
    await asyncio.sleep(0)
    assert not second_entered.is_set()
    assert len(module._local_locks) == 1
    release.set()
    await asyncio.gather(owner, waiter)
    assert second_entered.is_set()
    assert len(module._local_locks) == 0
