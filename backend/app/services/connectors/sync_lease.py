"""Scalar connector execution leases, with the work kernel's CAS/effect fence.

The lease is an Object (I-GRAPH-02): it participates in no graph traversal or
access resolution. PostgreSQL locks the lease row in the same transaction as
each record effect. A process lock is used only for single-process development
stores; it is never presented as distributed ownership.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

from jvspatial.core import Object
from jvspatial.core.context import get_default_context, graph_transaction

LEASE_SECONDS = 60.0
_dev_locks: dict[tuple[int, int, str], asyncio.Lock] = {}


class ConnectorSyncLease(Object):
    """Queue-shaped exclusive ownership, independent of mutable vendor cursors."""

    connector_id: str = ""
    workspace_id: str = ""
    principal_id: str = ""
    token: str = ""
    fence: int = 0
    expires_at: str = ""


class SyncLeaseLost(RuntimeError):
    """A reclaimed/expired worker has no authority to commit."""


@dataclass(frozen=True)
class SyncLease:
    object_id: str
    connector_id: str
    workspace_id: str
    principal_id: str
    token: str
    fence: int


current_sync_lease: ContextVar[SyncLease | None] = ContextVar(
    "connector_sync_lease", default=None
)


async def save_connector_changes(connector, *, fields: tuple[str, ...]) -> None:
    """Vendor refresh/cursor writes share the current worker's fence.

    Outside sync, the adapter's authenticated setup path retains its existing
    persistence contract. During sync only named fields are copied to a fresh
    node, so a pulled stale instance cannot overwrite newer binding settings.
    """
    lease = current_sync_lease.get()
    if lease is None:
        await connector.save()
        return
    from app.agentive.nodes import Connector

    async with sync_effect(lease):
        fresh = await Connector.get(connector.id)
        if (
            fresh is None
            or fresh.workspace_id != lease.workspace_id
            or fresh.owner != lease.principal_id
        ):
            raise SyncLeaseLost("Connector binding is no longer current")
        for field in fields:
            setattr(fresh, field, getattr(connector, field))
        await fresh.save()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expiry(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _object_id(connector_id: str) -> str:
    return "o.ConnectorSyncLease." + hashlib.sha256(connector_id.encode()).hexdigest()


@asynccontextmanager
async def _transaction(connector_id: str):
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
    )

    database = get_default_context().database
    if graph_transaction_available(database):
        async with graph_transaction(database) as graph:
            yield graph
        return
    concrete = database
    while getattr(concrete, "inner", None) is not None:
        concrete = concrete.inner
    from app.config import settings

    if type(concrete).__module__ not in {
        "jvspatial.db.jsondb",
        "jvspatial.memory",
    } or not (
        settings.DEBUG
        or os.environ.get("TESTING") == "1"
        or "PYTEST_CURRENT_TEST" in os.environ
    ):
        raise RuntimeError("Connector sync requires transactional durable storage")
    key = (id(concrete), id(asyncio.get_running_loop()), connector_id)
    async with _dev_locks.setdefault(key, asyncio.Lock()):
        yield get_default_context()


async def _cas(database, object_id: str, current: dict, updates: dict) -> bool:
    query = {
        "id": object_id,
        **{
            f"context.{key}": current[key]
            for key in (
                "connector_id",
                "workspace_id",
                "principal_id",
                "token",
                "fence",
                "expires_at",
            )
        },
    }
    result = await database.find_one_and_update(
        "object",
        query,
        {"$set": {f"context.{key}": value for key, value in updates.items()}},
    )
    return result is not None


async def claim_sync(
    connector, *, lease_seconds: float = LEASE_SECONDS
) -> SyncLease | None:
    """Atomically claim a free/expired connector binding, or report it busy."""
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    scope = {
        "connector_id": str(connector.id),
        "workspace_id": str(connector.workspace_id),
        "principal_id": str(connector.owner),
    }
    if not all(scope.values()):
        raise ValueError("Connector sync requires an immutable workspace and owner")
    oid = _object_id(connector.id)
    async with _transaction(connector.id) as graph:
        await ConnectorSyncLease.create_if_absent(id=oid, **scope)
        row = await graph.database.get("object", oid)
        current = dict(row["context"])
        if any(current[key] != value for key, value in scope.items()):
            raise SyncLeaseLost("Connector ownership changed; reinstall its binding")
        if current["token"] and _expiry(current["expires_at"]) > _now():
            return None
        token, fence = secrets.token_hex(24), int(current["fence"]) + 1
        if not await _cas(
            graph.database,
            oid,
            current,
            {
                "token": token,
                "fence": fence,
                "expires_at": (_now() + timedelta(seconds=lease_seconds)).isoformat(),
            },
        ):
            return None
        return SyncLease(object_id=oid, **scope, token=token, fence=fence)


@asynccontextmanager
async def sync_effect(lease: SyncLease, *, renew: bool = False) -> AsyncIterator:
    """CAS locks the current lease before yielding any graph effects."""
    async with _transaction(lease.connector_id) as graph:
        row = await graph.database.get("object", lease.object_id)
        current = dict((row or {}).get("context") or {})
        expected = {
            "connector_id": lease.connector_id,
            "workspace_id": lease.workspace_id,
            "principal_id": lease.principal_id,
            "token": lease.token,
            "fence": lease.fence,
        }
        if any(current.get(key) != value for key, value in expected.items()) or (
            _expiry(current.get("expires_at", "")) <= _now()
        ):
            raise SyncLeaseLost("Connector sync lease is no longer current")
        expiry = (
            (_now() + timedelta(seconds=LEASE_SECONDS)).isoformat()
            if renew
            else current["expires_at"]
        )
        if not await _cas(
            graph.database, lease.object_id, current, {"expires_at": expiry}
        ):
            raise SyncLeaseLost("Connector sync lease is no longer current")
        yield graph


async def renew_sync(lease: SyncLease) -> None:
    """Renew only while the exact lease is still unexpired and current."""
    async with sync_effect(lease, renew=True):
        pass


async def release_sync(lease: SyncLease) -> None:
    """Release this owner without clearing a successor's lease."""
    async with _transaction(lease.connector_id) as graph:
        row = await graph.database.get("object", lease.object_id)
        current = dict((row or {}).get("context") or {})
        if current.get("token") == lease.token and current.get("fence") == lease.fence:
            await _cas(
                graph.database,
                lease.object_id,
                current,
                {"token": "", "expires_at": ""},
            )
