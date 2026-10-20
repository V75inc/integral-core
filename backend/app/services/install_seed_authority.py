"""Narrow relation-read admission for a human's not-yet-active App install.

Normal data readers must never see an installing App. The installer still needs
its own canonical seed relations validated before activation. This task-local
context names that single effect; every admission rechecks live authority.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator, Literal

from app.schemas.policy import Decision, Subject


@dataclass(frozen=True)
class _InstallSeedRead:
    app_id: str
    actor_id: str
    task: asyncio.Task[object] | None


_SEED_STATES = frozenset({"installing", "awaiting_settings"})


_scope: ContextVar[_InstallSeedRead | None] = ContextVar(
    "install_seed_read", default=None
)


@contextmanager
def install_seed_read_scope(app_id: str, actor_id: str) -> Iterator[None]:
    """Name the installer effect; this alone grants no resource permission."""
    token = _scope.set(_InstallSeedRead(app_id, actor_id, asyncio.current_task()))
    try:
        yield
    finally:
        _scope.reset(token)


async def may_read_install_seed_relation(
    decision: Decision,
    subject: Subject,
    *,
    source_track_id: str,
    target_kind: Literal["entry", "track"],
    target_id: str,
) -> bool:
    """Admit one canonical seed relation using current installer authority."""
    context = _scope.get()
    if (
        decision.allowed
        or decision.reason != "default_human_policy_denied"
        or context is None
        or context.task is not asyncio.current_task()
        or subject.kind != "human"
        or subject.id != context.actor_id
    ):
        return False

    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.models.edges import EXCLUDED_FROM
    from app.models.nodes import App
    from app.services.permissions import can_admin_app, get_user_node
    from app.services.resource_access_blocks import resource_blocked
    from app.services.resource_location import locate_resource
    from app.services.workspace_permissions import can_access_workspace
    from app.services.workspace_suspension import workspace_suspended

    with isolated_permissions_cache():
        app = await App.find_one({"id": context.app_id})
        if app is None or app.lifecycle_state not in _SEED_STATES:
            return False
        source = await locate_resource(
            "track", source_track_id, allowed_app_states=_SEED_STATES
        )
        target = await locate_resource(
            target_kind, target_id, allowed_app_states=_SEED_STATES
        )
        if (
            source is None
            or target is None
            or source.app_id != app.id
            or target.app_id != app.id
            or source.workspace_id != app.workspace_id
            or target.workspace_id != app.workspace_id
        ):
            return False
        user = await get_user_node(subject.id)
        if (
            user is None
            or await workspace_suspended(user.id, app.workspace_id)
            or await can_access_workspace(user.id, app.workspace_id) == "none"
            or not await can_admin_app(user.id, app.id)
        ):
            return False
        if await EXCLUDED_FROM.count(
            {
                "source": user.id,
                "target": {
                    "$in": [app.id, source_track_id, target.track_id, target_id]
                },
            }
        ):
            return False
        if await resource_blocked(user.id, "track", source_track_id, location=source):
            return False
        return not await resource_blocked(
            user.id, target_kind, target_id, location=target
        )
