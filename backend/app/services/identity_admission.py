"""Current participant rows admitted before any workspace authorization lock."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import AsyncIterator, Iterable

from jvspatial.api.auth.models import User as AuthUser
from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError
from app.models.nodes import User
from app.services.authority_facts import (
    primary_auth_record,
    primary_node_record,
    required_node_text,
)
from app.services.entry_write_scope import is_postgres_entry_transaction

_active: ContextVar[tuple[object, object, frozenset[str], frozenset[str]] | None] = (
    ContextVar("integral_identity_admission", default=None)
)


async def _participant_profile(principal_id: str) -> User:
    """Resolve current profile including disabled affected subjects, without allow."""
    context = get_default_context()
    if principal_id.startswith("o.User."):
        records = await context.database.find(
            "node",
            {
                "entity": User._entity_name(),
                "$or": [{"context.user_id": principal_id}, {"user_id": principal_id}],
            },
            limit=2,
        )
        if len(records) != 1:
            raise AuthorizationUnavailableError()
        record = records[0]
        if required_node_text(record, "user_id") != principal_id:
            raise AuthorizationUnavailableError()
    else:
        record = await primary_node_record(context.database, User, principal_id)
        if record is None:
            raise AuthorizationUnavailableError()
    auth_id = required_node_text(record, "user_id", allow_empty=True)
    if auth_id:
        records = await context.database.find(
            "node",
            {
                "entity": User._entity_name(),
                "$or": [{"context.user_id": auth_id}, {"user_id": auth_id}],
            },
            limit=2,
        )
        if (
            len(records) != 1
            or records[0]["id"] != record["id"]
            or not auth_id.startswith("o.User.")
        ):
            raise AuthorizationUnavailableError()
        if await primary_auth_record(context.database, auth_id) is None:
            raise AuthorizationUnavailableError()
    profile = await context._deserialize_entity(User, record)
    if profile is None:
        raise AuthorizationUnavailableError()
    return profile


async def resolve_current_profile(
    principal_id: str, *, require_active: bool = True
) -> User:
    """Resolve exact current identity; inactive resolution is cleanup-only data."""
    from app.api.errors import InsufficientPermissionsError
    from app.services.permissions import get_user_node

    profile = await _participant_profile(principal_id)
    if require_active and await get_user_node(profile.id) is None:
        raise InsufficientPermissionsError(message="Access denied")
    return profile


async def _hold_row_shared(
    database, collection: str, row_id: str, entity: str, *, return_row: bool = False
):
    """Hold a shared lock on one row until this transaction ends; False if the row is gone.

    Admission must keep an account from being disabled or re-homed while a write by or about it
    is in flight. That needs writers to exclude the account's *mutators*, not each other. An
    exclusive lock here made every parallel write by the same user queue on one row (a pay run
    spent most of its wall time waiting); a shared lock lets them proceed together while any
    mutator, which takes the exclusive lock, still waits for all of them.
    """
    concrete = database
    seen: set[int] = set()
    while getattr(concrete, "inner", None) is not None and id(concrete) not in seen:
        seen.add(id(concrete))
        concrete = concrete.inner
    from jvspatial.db.postgres import _safe_collection

    schema = _safe_collection(concrete._db.schema_name)
    table = _safe_collection(collection)
    async with concrete._operation_lock:
        concrete._ensure_active()
        row = await concrete._connection.fetchrow(
            f"SELECT id, data FROM {schema}.{table} WHERE id = $1 AND entity = $2 FOR SHARE",
            row_id,
            entity,
        )
    if not return_row:
        return row is not None
    if row is None:
        return None
    data = row["data"]
    if isinstance(data, (str, bytes)):
        import json

        data = json.loads(data)
    return data


@asynccontextmanager
async def identity_admission(
    principal_ids: Iterable[str], *, active_principal_ids: Iterable[str] | None = None
) -> AsyncIterator[None]:
    """Lock explicit participants; active actors cannot be disabled cleanup subjects."""
    from app.api.errors import InsufficientPermissionsError
    from app.services.permissions import get_user_node

    requested = frozenset(principal_ids)
    required_active = (
        requested if active_principal_ids is None else frozenset(active_principal_ids)
    )
    if not required_active.issubset(requested) or any(
        type(value) is not str or not value or value != value.strip()
        for value in requested
    ):
        raise AuthorizationUnavailableError()
    database = get_default_context().database
    active = _active.get()
    if active is not None:
        if (
            active[0] is not asyncio.current_task()
            or active[1] is not database
            or not requested.issubset(active[2])
        ):
            raise AuthorizationUnavailableError()
        for principal_id in required_active:
            if await get_user_node(principal_id) is None:
                raise InsufficientPermissionsError(message="Access denied")
        yield
        return
    profiles = {
        profile.id: profile
        for profile in [
            await _participant_profile(identity) for identity in sorted(requested)
        ]
    }
    admitted = (
        requested
        | frozenset(profiles)
        | frozenset(profile.user_id for profile in profiles.values() if profile.user_id)
    )
    if is_postgres_entry_transaction(database):
        for identity_id in sorted(
            {profile.user_id for profile in profiles.values() if profile.user_id}
        ):
            locked = await _hold_row_shared(
                database, "object", identity_id, AuthUser._entity_name()
            )
            if (
                not locked
                or await primary_auth_record(database, identity_id) is None
            ):
                raise AuthorizationUnavailableError()
        for profile_id in sorted(profiles):
            locked = await _hold_row_shared(
                database, "node", profile_id, User._entity_name()
            )
            current = await _participant_profile(profile_id)
            if not locked or current.user_id != profiles[profile_id].user_id:
                raise AuthorizationUnavailableError()
    for principal_id in required_active:
        if await get_user_node(principal_id) is None:
            raise InsufficientPermissionsError(message="Access denied")
    token = _active.set((asyncio.current_task(), database, admitted, required_active))
    try:
        yield
    finally:
        _active.reset(token)
