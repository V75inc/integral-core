"""Private task-owned provenance for proposals minted by a trusted broker call."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from app.schemas.agentive.work import WorkExecutionContext

_active: ContextVar[tuple[object, dict[str, Any]] | None] = ContextVar(
    "integral_proposal_origin", default=None
)


def origin_for(
    context: WorkExecutionContext, capability_key: str, mandate: str
) -> dict[str, Any]:
    """Describe an invocation without storing its lease credential."""
    return {
        "version": "work-proposal-v1",
        "work_item_id": context.work_item_id,
        "run_id": context.run_id,
        "attempt": context.attempt,
        "principal_id": context.principal_id,
        "workspace_id": context.workspace_id,
        "thread_id": context.thread_id,
        "effect_key": context.effect_key,
        "capability_key": capability_key,
        "mandate_fingerprint": mandate,
    }


@contextmanager
def bind_proposal_origin(proof: dict[str, Any]) -> Iterator[None]:
    """Bind server-derived proof for this adapter task, never inherited tasks."""
    token = _active.set((asyncio.current_task(), dict(proof)))
    try:
        yield
    finally:
        _active.reset(token)


def current_proposal_origin(
    user_id: str, workspace_id: str | None
) -> dict[str, Any] | None:
    """Unbound callers and mismatched/inherited scopes mint ordinary proposals."""
    active = _active.get()
    if active is None or active[0] is not asyncio.current_task():
        return None
    proof = active[1]
    if (
        proof.get("principal_id") != user_id
        or proof.get("workspace_id") != workspace_id
    ):
        return None
    return dict(proof)
