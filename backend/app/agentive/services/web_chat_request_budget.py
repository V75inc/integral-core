"""Detached PostgreSQL request budget for the full-page public visitor host.

This is an admission rate cap, not a throttle for staff or a reservation of
back-office pool/model capacity. No ambient adapter, default policy, mount,
network call or public link is configured. The future host must supply a
dedicated bounded public pool and reserve model/worker headroom separately.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass
from typing import Callable

from jvspatial.db.postgres import PostgresDB, PostgresTransaction, current_tenant
from jvspatial.db.transaction import transaction_context

from app.agentive.connectors.external_chat import (
    ExternalChatConversation,
    ExternalChatProvision,
)
from app.agentive.services.public_web_chat import PublicWebChatScope
from app.agentive.services.web_chat_admission import VerifiedFullPageSession
from app.agentive.services.web_chat_browser_transport import Operation
from app.models.web_chat_budget_counter import WebChatBudgetCounter

_NAMESPACE = "integral-public-web-chat-budget-v1"
_ENTITY = "WebChatBudgetCounter"
_DIGEST = re.compile(r"[0-9a-f]{64}")


def _hash(*values: str | int) -> str:
    wire = json.dumps(values, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(wire.encode("ascii")).hexdigest()


def _id(*values: str) -> str:
    return f"o.{_ENTITY}.{_hash(_NAMESPACE, *values)}"


def _key(value: str) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 256 and value == value.strip()


@dataclass(frozen=True)
class WebChatBudgetPolicy:
    """Trusted fixed-window limits; all workers sharing a store use one policy."""

    window_seconds: int
    public_starts: int
    public_sends: int
    workspace_starts: int
    workspace_sends: int
    provision_starts: int
    provision_sends: int
    visitor_sends: int

    def __post_init__(self) -> None:
        """Prevent accidental unlimited counters and bound stored visitor maps."""
        values = (
            self.window_seconds,
            self.public_starts,
            self.public_sends,
            self.workspace_starts,
            self.workspace_sends,
            self.provision_starts,
            self.provision_sends,
            self.visitor_sends,
        )
        if (
            not all(type(value) is int and value > 0 for value in values)
            or not 1 <= self.window_seconds <= 3600
            or not self.provision_starts
            <= self.workspace_starts
            <= self.public_starts
            <= 10_000
            or not self.provision_sends
            <= self.workspace_sends
            <= self.public_sends
            <= 10_000
            or not self.visitor_sends <= self.provision_sends <= 1000
        ):
            raise ValueError("Invalid public Web Chat rate policy")

    @property
    def fingerprint(self) -> str:
        """Fail closed rather than mixing incompatible limits across workers."""
        return _hash(
            self.window_seconds,
            self.public_starts,
            self.public_sends,
            self.workspace_starts,
            self.workspace_sends,
            self.provision_starts,
            self.provision_sends,
            self.visitor_sends,
        )


@dataclass(frozen=True)
class _Bucket:
    record_id: str
    starts: int
    sends: int
    visitor: str | None = None
    tracks_visitors: bool = False


def _read_counter(
    bucket: _Bucket, row: dict | None, window: int, fingerprint: str, visitor_limit: int
) -> WebChatBudgetCounter:
    fresh = WebChatBudgetCounter(
        id=bucket.record_id, policy=fingerprint, window=window, starts=0, sends=0
    )
    if row is None:
        return fresh
    if (
        row.get("id") != bucket.record_id
        or row.get("entity") != _ENTITY
        or not isinstance(row.get("context"), dict)
    ):
        raise ValueError("Invalid Web Chat counter")
    data = row["context"]
    if data.get("policy") != fingerprint or type(data.get("window")) is not int:
        raise ValueError("Incompatible Web Chat rate policy")
    if data["window"] > window:
        # A skewed or rolled-back clock cannot refill an already started window.
        raise ValueError("Invalid Web Chat clock")
    if data["window"] < window:
        return fresh
    starts, sends, visitors = (
        data.get("starts"),
        data.get("sends"),
        data.get("visitor_sends"),
    )
    if (
        type(starts) is not int
        or not 0 <= starts <= bucket.starts
        or type(sends) is not int
        or not 0 <= sends <= bucket.sends
        or not isinstance(visitors, dict)
        or len(visitors) > sends
        or any(
            not isinstance(key, str)
            or not _DIGEST.fullmatch(key)
            or type(count) is not int
            or not 1 <= count <= visitor_limit
            for key, count in visitors.items()
        )
        or (not bucket.tracks_visitors and visitors)
    ):
        raise ValueError("Invalid Web Chat counter")
    if bucket.tracks_visitors and sum(visitors.values()) != sends:
        raise ValueError("Invalid Web Chat visitor counters")
    fresh.starts = starts
    fresh.sends = sends
    fresh.visitor_sends = dict(visitors)
    return fresh


class PostgresWebChatRequestBudget:
    """Implement BrowserWebChatController's RequestBudget across workers.

    Each adapter instance belongs to its own event loop. A bounded local gate
    keeps it from queuing unbounded pool waiters; a short end-to-end deadline
    covers pool/lock/store work. Only an acknowledged commit grants admission.
    Counters hold no credentials, message content, or raw visitor/scope IDs.
    """

    def __init__(
        self,
        *,
        database: PostgresDB,
        policy: WebChatBudgetPolicy,
        acquire_seconds: float,
        max_parallel_acquisitions: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(database, PostgresDB):
            raise TypeError("Public Web Chat request budget requires PostgreSQL")
        if not isinstance(policy, WebChatBudgetPolicy):
            raise TypeError("Public Web Chat request budget requires explicit limits")
        if (
            type(acquire_seconds) not in (float, int)
            or not math.isfinite(acquire_seconds)
            or not 0.01 <= acquire_seconds <= 2
            or type(max_parallel_acquisitions) is not int
            or not 1 <= max_parallel_acquisitions <= 32
        ):
            raise ValueError("Invalid public Web Chat budget acquisition limits")
        self._database = database
        self._policy = policy
        self._fingerprint = policy.fingerprint
        self._clock = clock
        self._timeout = acquire_seconds
        self._max_parallel = max_parallel_acquisitions
        self._active: set[asyncio.Task[bool]] = set()

    def _finished(self, task: asyncio.Task[bool]) -> None:
        self._active.discard(task)
        if not task.cancelled():
            task.exception()  # A timed-out caller no longer awaits cleanup.

    def _buckets(
        self,
        operation: Operation,
        provision: ExternalChatProvision,
        session: VerifiedFullPageSession | None,
    ) -> tuple[_Bucket, ...]:
        if (
            not isinstance(provision, ExternalChatProvision)
            or provision.connector_kind != "public_web_chat"
            or not _key(provision.workspace_id)
            or not _key(provision.provision_id)
        ):
            return ()
        visitor = None
        if operation == "start" and session is None:
            pass
        elif (
            operation == "send"
            and isinstance(session, VerifiedFullPageSession)
            and isinstance(session.conversation, ExternalChatConversation)
            and session.conversation.provision == provision
            and isinstance(session.scope, PublicWebChatScope)
            and session.scope.workspace_id == provision.workspace_id
            and _key(session.scope.visitor_id)
        ):
            visitor = _hash(
                _NAMESPACE,
                provision.workspace_id,
                provision.provision_id,
                session.scope.visitor_id,
            )
        else:
            return ()
        p = self._policy
        return (
            _Bucket(_id("public"), p.public_starts, p.public_sends),
            _Bucket(
                _id("workspace", provision.workspace_id),
                p.workspace_starts,
                p.workspace_sends,
            ),
            _Bucket(
                _id("provision", provision.workspace_id, provision.provision_id),
                p.provision_starts,
                p.provision_sends,
                visitor,
                tracks_visitors=True,
            ),
        )

    async def _take(self, operation: Operation, buckets: tuple[_Bucket, ...]) -> bool:
        async with transaction_context(self._database) as tx:
            if not isinstance(tx, PostgresTransaction):
                return False
            # Every request shares this short-lived public lock already. One
            # order covers all buckets; never take workspace, provider, or
            # graph-authorization locks from this isolated transaction.
            await tx.advisory_lock(_NAMESPACE, "public")
            now = self._clock()
            if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
                return False
            window = math.floor(now / self._policy.window_seconds)
            rows = await tx.get_many("object", [bucket.record_id for bucket in buckets])
            updates = []
            for bucket in buckets:
                # The provision row also holds per-visitor sends even for a start.
                counter = _read_counter(
                    bucket,
                    rows.get(bucket.record_id),
                    window,
                    self._fingerprint,
                    self._policy.visitor_sends,
                )
                if operation == "start":
                    if counter.starts >= bucket.starts:
                        return False
                    counter.starts += 1
                else:
                    if counter.sends >= bucket.sends:
                        return False
                    if bucket.visitor is not None:
                        prior = counter.visitor_sends.get(bucket.visitor, 0)
                        if prior >= self._policy.visitor_sends:
                            return False
                        counter.visitor_sends[bucket.visitor] = prior + 1
                    counter.sends += 1
                updates.append(counter)
            for counter in updates:
                await tx.save("object", await counter.export())
        return True

    async def __call__(
        self,
        operation: Operation,
        provision: ExternalChatProvision,
        session: VerifiedFullPageSession | None,
    ) -> bool:
        """Debit exactly one valid public start/send, or fail closed."""
        # Postgres lock authority includes ambient tenant scope. Using one here
        # would silently split the shared public cap by implicit caller state.
        if current_tenant() is not None or len(self._active) >= self._max_parallel:
            return False
        buckets = self._buckets(operation, provision, session)
        if not buckets:
            return False
        task = asyncio.create_task(self._take(operation, buckets))
        self._active.add(task)
        task.add_done_callback(self._finished)
        try:
            async with asyncio.timeout(self._timeout):
                return await asyncio.shield(task)
        except asyncio.CancelledError:
            task.cancel()
            raise
        except Exception:
            # No retries/capacity grants on timeouts, malformed state, or an
            # ambiguous commit. A charged slot may be lost conservatively.
            # Cancellation/rollback may outlast the caller but still occupies
            # its slot until cleanup ends, so overload cannot pile up waiters.
            task.cancel()
            return False
