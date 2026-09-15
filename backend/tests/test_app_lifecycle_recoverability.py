"""Lifecycle recoverability unit tests (F0)."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.services.app_lifecycle import InstallTransaction
from app.services.hooks.track_aliases import (
    clear_workspace_track_aliases,
    register_track_aliases_from_manifest,
    track_type_want_set,
)


@pytest.mark.asyncio
async def test_install_transaction_checkpoint_and_compensate(monkeypatch):
    created: List[Dict[str, Any]] = []

    class FakeAttempt:
        @classmethod
        async def create(cls, **kwargs):
            created.append(kwargs)
            return kwargs

    monkeypatch.setattr(
        "app.models.install_attempt.InstallAttempt",
        FakeAttempt,
    )

    compensated: List[str] = []

    async def undo_a():
        compensated.append("a")

    async def undo_b():
        compensated.append("b")

    txn = InstallTransaction(
        workspace_id="ws1",
        actor_id="u1",
        package_slug="hello",
        app_id="app1",
    )
    await txn.checkpoint("app_create")
    txn.record("a", undo_a)
    txn.record("b", undo_b)
    n = await txn.compensate()
    assert n == 2
    assert compensated == ["b", "a"]
    assert any(c.get("step") == "app_create" for c in created)
    assert any(c.get("status") == "rolled_back" for c in created)


def test_track_aliases_from_manifest():
    clear_workspace_track_aliases("ws_aliases")
    register_track_aliases_from_manifest(
        "ws_aliases",
        {"app": {"track_aliases": [{"aliases": ["projects", "customer_projects"]}]}},
    )
    want = track_type_want_set("projects", "ws_aliases")
    assert want == {"projects", "customer_projects"}
    clear_workspace_track_aliases("ws_aliases")
