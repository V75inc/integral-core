"""Private task-owned provenance for proposals minted by a trusted broker call."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from app.schemas.agentive.work import WorkExecutionContext

_active: ContextVar[tuple[object, dict[str, Any], bool, bool] | None] = ContextVar(
    "integral_proposal_origin", default=None
)
_deferred_invocation: ContextVar[tuple[object, WorkExecutionContext] | None] = (
    ContextVar("integral_deferred_proposal_invocation", default=None)
)


@contextmanager
def bind_deferred_proposal(context: WorkExecutionContext) -> Iterator[None]:
    """Mark the exact broker invocation that will park a native frontier."""
    token = _deferred_invocation.set((asyncio.current_task(), context))
    try:
        yield
    finally:
        _deferred_invocation.reset(token)


def origin_for(
    context: WorkExecutionContext, capability_key: str, mandate: str
) -> dict[str, Any]:
    """Describe an invocation without storing its lease credential."""
    proof = {
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
    if _deferred_invocation.get() == (asyncio.current_task(), context):
        proof["requires_deferred_link"] = True
    return proof


@contextmanager
def bind_proposal_origin(
    proof: dict[str, Any],
    *,
    interactive_chat: bool = False,
    check_permission: bool = False,
) -> Iterator[None]:
    """Bind server-derived proof for this adapter task, never inherited tasks."""
    token = _active.set(
        (asyncio.current_task(), dict(proof), interactive_chat, check_permission)
    )
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


def interactive_resident_proposal() -> bool:
    """Only the native chat broker selects the resident's confirmation policy."""
    active = _active.get()
    return bool(active and active[0] is asyncio.current_task() and active[2])


def checking_resident_permission() -> bool:
    """Defer the review notification until a possible standing decision is resolved."""
    active = _active.get()
    return bool(active and active[0] is asyncio.current_task() and active[3])
