"""A hook may save its own entry without dispatching itself recursively."""

import asyncio
from types import SimpleNamespace

import pytest

from app.models.nodes import EntryType
from app.services.hooks import entry_save_runtime, registry, resolver, tool_dispatch

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest.fixture
def hook_entry(monkeypatch):
    async def entry_type(_id):
        return SimpleNamespace(
            name="Note", form_schema={"_manifest_entry_type_key": "note"}
        )

    monkeypatch.setattr(EntryType, "get", entry_type)
    monkeypatch.setattr(
        registry, "get_workspace_tools", lambda _ws: {"mirror": {"key": "mirror"}}
    )
    monkeypatch.setattr(
        resolver,
        "find_matching_bindings",
        lambda *args: [{"key": "mirror-save", "mode": "tool", "tool": "mirror"}],
    )
    return SimpleNamespace(id="entry-hook", type_id="type-note")


async def dispatch(entry):
    await entry_save_runtime.run_entry_save_hooks(
        entry=entry,
        workspace_id="workspace-hook",
        actor_id="user-hook",
        hook_point="entry.update",
    )


async def test_self_save_skips_active_hook_and_next_save_runs_it_again(
    hook_entry, monkeypatch
):
    calls = []

    async def run(*args):
        calls.append(1)
        assert len(calls) == 1
        await dispatch(hook_entry)

    monkeypatch.setattr(tool_dispatch, "run_tool", run)
    await dispatch(hook_entry)
    assert len(calls) == 1
    calls.clear()
    await dispatch(hook_entry)
    assert len(calls) == 1


async def test_failed_hook_releases_recursion_guard(hook_entry, monkeypatch):
    calls = []

    async def run(*args):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("hook probe")

    monkeypatch.setattr(tool_dispatch, "run_tool", run)
    await dispatch(hook_entry)
    await dispatch(hook_entry)
    assert len(calls) == 2


async def test_inherited_context_does_not_suppress_independent_task(
    hook_entry, monkeypatch
):
    calls = []

    async def run(*args):
        calls.append(asyncio.current_task())
        if len(calls) == 1:
            await asyncio.create_task(dispatch(hook_entry))

    monkeypatch.setattr(tool_dispatch, "run_tool", run)
    await dispatch(hook_entry)
    assert len(calls) == 2 and calls[0] is not calls[1]
