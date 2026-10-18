"""Order participating collaboration effects and their durable audit intent."""

from __future__ import annotations

import asyncio
import inspect
import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Iterable

from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError
from app.services.entry_write_scope import is_postgres_entry_transaction
from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource

logger = logging.getLogger(__name__)


@dataclass
class _SharingUnit:
    workspace_id: str
    owner_task: object
    database: object
    outbox_ids: list[str] = field(default_factory=list)


_active: ContextVar[_SharingUnit | None] = ContextVar(
    "integral_sharing_unit", default=None
)


@asynccontextmanager
async def sharing_write_scope(
    workspace_id: str,
    *,
    principal_ids: Iterable[str] = (),
    active_principal_ids: Iterable[str] | None = None,
):
    """Join same-task units; never let child tasks inherit another owner's unit."""
    # Import at admission: importing the transaction package registers API
    # modules, whose collaboration services also depend on this scope.
    from app.services.identity_admission import identity_admission
    from app.services.membership_write_scope import membership_write_scope

    existing = _active.get()
    if existing is not None:
        if (
            existing.workspace_id != workspace_id
            or existing.owner_task is not asyncio.current_task()
            or existing.database is not get_default_context().database
        ):
            raise AuthorizationUnavailableError()
        async with identity_admission(
            principal_ids, active_principal_ids=active_principal_ids
        ):
            yield
        return
    async with membership_write_scope(
        workspace_id,
        principal_ids=principal_ids,
        active_principal_ids=active_principal_ids,
    ):
        unit = _SharingUnit(
            workspace_id, asyncio.current_task(), get_default_context().database
        )
        token = _active.set(unit)
        try:
            yield
        finally:
            _active.reset(token)
    # An outer owner still controls commit; its persisted intents are recovered
    # by the normal outbox consumer. Never emit facts from its open transaction.
    if is_postgres_entry_transaction(get_default_context().database):
        return
    from app.services.app_operations.event_outbox import deliver_operation_event

    for outbox_id in unit.outbox_ids:
        try:
            await deliver_operation_event(
                outbox_id=outbox_id, database=get_default_context().database
            )
        except Exception:
            logger.exception("Collaboration audit delivery pending: %s", outbox_id)


def collaboration_mutation(function):
    """Locate before locking, then recheck exact rooted containment after admission."""
    signature = inspect.signature(function)

    @wraps(function)
    async def invoke(*args: Any, **kwargs):
        from app.runtime.composition import require_capability

        require_capability("collaboration")
        # Import after the sharing service has finished defining its commands.
        from app.api.errors import BadRequestError, ResourceNotFoundError

        bound = signature.bind(*args, **kwargs)
        kind = bound.arguments["resource_type"]
        identity = bound.arguments["resource_id"]
        if type(kind) is not str or kind not in {"app", "track", "entry"}:
            raise BadRequestError(message="Invalid resource type")
        if type(identity) is not str or not identity or identity != identity.strip():
            raise BadRequestError(message="Invalid resource identity")
        location = await locate_resource(
            kind, identity, allowed_app_states=MANAGEMENT_APP_STATES
        )
        if location is None:
            raise ResourceNotFoundError(message="Resource not found")
        principals = tuple(
            value
            for name, value in bound.arguments.items()
            if name
            in {
                "actor_user_id",
                "collaborator_user_id",
                "user_id_to_remove",
                "user_id_to_exclude",
                "user_id_to_restore",
            }
            and isinstance(value, str)
            and value
        )
        async with sharing_write_scope(
            location.workspace_id,
            principal_ids=principals,
            active_principal_ids=(
                (bound.arguments["actor_user_id"],)
                if function.__name__ in {"remove_collaborator", "remove_exclusion"}
                else principals
            ),
        ):
            current = await locate_resource(
                kind, identity, allowed_app_states=MANAGEMENT_APP_STATES
            )
            if current is None or current.workspace_id != location.workspace_id:
                raise AuthorizationUnavailableError()
            result = await function(*args, **kwargs)
        return result

    return invoke


async def emit_sharing_change(*, clock: dict, emitter: Any, **event: Any) -> None:
    """Record the fact with its relationship clock; JSON retains its weaker sink."""
    database = get_default_context().database
    if not is_postgres_entry_transaction(database):
        await emitter(**event)
        return
    unit = _active.get()
    if (
        unit is None
        or unit.owner_task is not asyncio.current_task()
        or unit.database is not database
    ):
        raise AuthorizationUnavailableError()
    from app.services.app_operations.event_outbox import insert_revision_event

    outbox_id = await insert_revision_event(
        transaction=database,
        workspace_id=unit.workspace_id,
        namespace="sharing-grant-history-v2",
        subject_id=clock["id"],
        revision=clock["context"]["revision"],
        event=event,
    )
    unit.outbox_ids.append(outbox_id)


def register_sharing_outbox(outbox_id: str | None) -> None:
    """Attach a committed-intent identity to its exact current sharing owner."""
    if outbox_id is None:
        return
    unit = _active.get()
    if (
        unit is None
        or unit.owner_task is not asyncio.current_task()
        or unit.database is not get_default_context().database
        or type(outbox_id) is not str
        or not outbox_id
        or outbox_id != outbox_id.strip()
    ):
        raise AuthorizationUnavailableError()
    if outbox_id not in unit.outbox_ids:
        unit.outbox_ids.append(outbox_id)
