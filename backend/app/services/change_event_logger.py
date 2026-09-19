"""Persist ChangeEvent records as DBLog rows in the logging database.

Mirrors jvspatial's INTERACTION / AUDIT pattern (see
``jvspatial.logging.service.BaseLoggingService.log_custom``): every change
event is stored as a single ``DBLog`` row whose ``log_level="CHANGE_EVENT"``
discriminates it from error logs and other custom-level rows that share the
same logging-database table.

Why this shape:
- No new Node type in the prime application graph (ChangeEvent rows do not
  pad the operational DB).
- One canonical storage shape for all audit-style logs (errors, interactions,
  change events) — DBLog with a log_level discriminator.
- jvspatial's logging API endpoints (``GET /api/logs``) work for free.

In-memory representation
------------------------
``ChangeEventEnvelope`` is the runtime shape passed through ``emit_change_event``
→ ``broadcast_change_event`` → WS subscribers + in-process consumer hooks. It is
NOT a jvspatial Node — it has no DB identity beyond the DBLog row it mirrors,
and consumers should treat the envelope as immutable once persisted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings

logger = logging.getLogger(__name__)

CHANGE_EVENT_LOG_LEVEL = "CHANGE_EVENT"


def is_change_event_enabled() -> bool:
    """Master kill switch: ``settings.CHANGE_EVENT_ENABLED`` (default True)."""
    return bool(settings.CHANGE_EVENT_ENABLED)


@dataclass
class ChangeEventEnvelope:
    """In-memory shape of a ChangeEvent. Mirrors one DBLog row.

    ``id`` is populated by ``ChangeEventLogger.persist`` from the DBLog row's
    id once stored; on a disabled / unpersisted emit it stays empty.

    ``details`` (Phase 3 Plan 03-05) is an additive structured-metadata slot.
    Today it carries the {failed_action, decision_reason, matched_policy_id}
    payload of ``policy.deny`` denial events emitted by the policy engine. It
    is persisted on DBLog.log_data["details"] and survives the Phase 2 D-04
    TTL reclaim (only ``before``/``after`` snapshots are nulled by reclaim).
    Phase 2 callers continue to omit it — the default is None.
    """

    ts: str
    actor_kind: str
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    scope: str
    before: Optional[Dict[str, Any]] = None
    after: Optional[Dict[str, Any]] = None
    actor_capability: Optional[str] = None
    snapshot_reclaimed_at: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    id: str = ""

    def to_wire(self) -> Dict[str, Any]:
        """Serialize for the WS broadcast — nested ``actor`` shape (ChangeEventResponse)."""
        return {
            "id": self.id,
            "ts": self.ts,
            "actor": {
                "kind": self.actor_kind,
                "id": self.actor_id,
                "capability": self.actor_capability,
            },
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "scope": self.scope,
            "before": self.before,
            "after": self.after,
            "details": self.details,
        }

    def to_wire_flat(self) -> Dict[str, Any]:
        """Serialize for the HTTP audit-log + events_polling endpoints.

        Flat actor fields (``actor_kind``, ``actor_id``, ``actor_capability``).
        Matches the previous Node-export shape so existing API consumers do not
        need to migrate.
        """
        return {
            "id": self.id,
            "ts": self.ts,
            "actor_kind": self.actor_kind,
            "actor_id": self.actor_id,
            "actor_capability": self.actor_capability,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "scope": self.scope,
            "before": self.before,
            "after": self.after,
            "details": self.details,
        }


def envelope_from_dblog(log: Any) -> ChangeEventEnvelope:
    """Reconstruct a ChangeEventEnvelope from a stored DBLog row."""
    data = getattr(log, "log_data", None) or {}
    logged_at = getattr(log, "logged_at", None)
    if isinstance(logged_at, datetime):
        ts = logged_at.isoformat()
    elif isinstance(logged_at, str):
        ts = logged_at
    else:
        ts = ""
    return ChangeEventEnvelope(
        id=getattr(log, "id", "") or "",
        ts=ts,
        actor_kind=data.get("actor_kind", "system"),
        actor_id=data.get("actor_id", ""),
        actor_capability=data.get("actor_capability"),
        action=getattr(log, "event_code", "") or data.get("action", ""),
        resource_type=data.get("resource_type", ""),
        resource_id=data.get("resource_id", ""),
        scope=getattr(log, "path", "") or data.get("scope", ""),
        before=data.get("before"),
        after=data.get("after"),
        snapshot_reclaimed_at=data.get("snapshot_reclaimed_at"),
        # Phase 3 Plan 03-05 — additive metadata slot. Round-trips through
        # DBLog.log_data["details"]. None on pre-Plan-05 rows.
        details=data.get("details"),
    )


def _envelope_log_data(envelope: ChangeEventEnvelope) -> Dict[str, Any]:
    """Build the ``log_data`` dict persisted on a DBLog row."""
    return {
        "message": envelope.action,
        "log_level": CHANGE_EVENT_LOG_LEVEL,
        "ts": envelope.ts,
        "actor_kind": envelope.actor_kind,
        "actor_id": envelope.actor_id,
        "actor_capability": envelope.actor_capability,
        "resource_type": envelope.resource_type,
        "resource_id": envelope.resource_id,
        "scope": envelope.scope,
        "before": envelope.before,
        "after": envelope.after,
        "snapshot_reclaimed_at": envelope.snapshot_reclaimed_at,
        # Phase 3 Plan 03-05 — additive metadata slot. Persisted on every row;
        # None on the Phase 2 happy paths and populated only by policy.deny
        # denial events emitted from policy_engine.evaluate.
        "details": envelope.details,
    }


class ChangeEventLogger:
    """Persistence + query for ChangeEvent records stored as DBLog rows.

    Storage shape: ``DBLog`` with ``log_level="CHANGE_EVENT"``. The
    discriminator keeps change-event rows queryable independent of other log
    levels (ERROR, CRITICAL, INTERACTION, AUDIT, …) that share the table.
    """

    def __init__(self, database_name: Optional[str] = None) -> None:
        self._database_name = (
            database_name or settings.DB_LOGGING_DB_NAME or "logs"
        ).strip() or "logs"

    @property
    def database_name(self) -> str:
        """Name of the jvspatial logging database this logger targets."""
        return self._database_name

    def _get_log_db(self) -> Any:
        from jvspatial.db import get_database_manager

        try:
            manager = get_database_manager()
            registered = manager.list_databases()
        except Exception as e:
            logger.warning("change_event_logger: database manager unavailable: %s", e)
            return None
        if self._database_name not in registered:
            logger.warning(
                "change_event_logger: logging database %r not registered "
                "(registered=%s); ChangeEvent emits will not be persisted",
                self._database_name,
                list(registered),
            )
            return None
        return manager.get_database(self._database_name)

    def _get_log_context(self) -> Any:
        from jvspatial.core.context import GraphContext

        log_db = self._get_log_db()
        if log_db is None:
            return None
        return GraphContext(database=log_db)

    async def ensure_indexes(self) -> None:
        """Ensure DBLog indexes exist on the routed logging database."""
        from jvspatial.logging.models import DBLog

        ctx = self._get_log_context()
        if ctx is None:
            return
        try:
            await ctx.ensure_indexes(DBLog)
        except Exception as e:
            logger.warning("change_event_logger: ensure_indexes failed: %s", e)

    async def persist(self, envelope: ChangeEventEnvelope) -> Any:
        """Persist the envelope as a DBLog row. Mutates ``envelope.id``."""
        from jvspatial.logging.models import DBLog

        ctx = self._get_log_context()
        if ctx is None:
            return None
        try:
            await ctx.ensure_indexes(DBLog)
        except Exception as e:
            logger.warning("change_event_logger: ensure_indexes failed: %s", e)

        try:
            ts_dt = datetime.fromisoformat(envelope.ts)
            if ts_dt.tzinfo is None:
                ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            ts_dt = datetime.now(timezone.utc)
            envelope.ts = ts_dt.isoformat()

        log_entry = DBLog(
            status_code=None,
            event_code=envelope.action,
            log_level=CHANGE_EVENT_LOG_LEVEL,
            path=envelope.scope,
            method="",
            logged_at=ts_dt,
            log_data=_envelope_log_data(envelope),
        )
        await log_entry.set_context(ctx)
        await log_entry.save()
        envelope.id = log_entry.id
        return log_entry

    async def find_all(
        self,
        *,
        scope: Optional[str] = None,
        actor_kind: Optional[str] = None,
    ) -> List[Any]:
        """Return all DBLog rows with ``log_level=CHANGE_EVENT`` matching filters.

        ``scope`` filters by ``DBLog.path`` (where envelope.scope lives).
        ``actor_kind`` filters by the value stored in ``log_data.actor_kind``.
        """
        from jvspatial.logging.models import DBLog

        ctx = self._get_log_context()
        if ctx is None:
            return []
        query: Dict[str, Any] = {
            "entity": "DBLog",
            "context.log_level": CHANGE_EVENT_LOG_LEVEL,
        }
        if scope:
            query["context.path"] = scope
        if actor_kind:
            query["context.log_data.actor_kind"] = actor_kind

        try:
            raw = await ctx.database.find("object", query)
        except Exception as e:
            logger.warning("change_event_logger: find failed: %s", e)
            return []

        results: List[DBLog] = []
        for row in raw:
            try:
                context_data = row.get("context", {}).copy()
                row_id = row.get("id", "")
                if "logged_at" in context_data and isinstance(
                    context_data["logged_at"], str
                ):
                    try:
                        context_data["logged_at"] = datetime.fromisoformat(
                            context_data["logged_at"].replace("Z", "+00:00")
                        )
                    except (ValueError, AttributeError):
                        context_data["logged_at"] = datetime.now(timezone.utc)
                entry = DBLog(id=row_id, **context_data)
                await entry.set_context(ctx)
                results.append(entry)
            except Exception as e:
                logger.warning(
                    "change_event_logger: skipping unparseable DBLog row %s: %s",
                    row.get("id"),
                    e,
                )
        return results

    async def find_reclaimable(self, *, cutoff: datetime, limit: int) -> List[Any]:
        """Return up to ``limit`` CHANGE_EVENT rows older than ``cutoff`` whose
        snapshot is unreclaimed.

        Pushes both predicates into the database query so the TTL sweep
        does not deserialize every change-event row every pass:

        * ``context.logged_at < cutoff`` — ISO-8601 strings compare
          lexicographically (all rows are written UTC by ``persist``).
        * ``context.log_data.snapshot_reclaimed_at`` absent / null.

        ``limit`` is mandatory and bounds the hydration: the predicate alone
        bounds the STEADY state, but the first pass after the TTL is crossed
        (or lowered, or after an outage longer than the TTL window) can match
        an arbitrarily large backlog. Callers page by calling repeatedly —
        reclaimed rows drop out of the predicate, so successive calls make
        progress without a cursor. Rows are ordered by ``id`` so a page is
        deterministic.

        Callers should still re-check ``logged_at`` on the hydrated row —
        the string comparison is a coarse filter, not the authority.
        """
        from jvspatial.logging.models import DBLog

        ctx = self._get_log_context()
        if ctx is None:
            return []
        if limit <= 0:
            return []
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)
        query: Dict[str, Any] = {
            "entity": "DBLog",
            "context.log_level": CHANGE_EVENT_LOG_LEVEL,
            "context.logged_at": {"$lt": cutoff.isoformat()},
            "context.log_data.snapshot_reclaimed_at": {"$exists": False},
        }
        try:
            raw = await ctx.database.find(
                "object", query, limit=limit, sort=[("id", 1)]
            )
        except Exception as e:
            logger.warning("change_event_logger: find_reclaimable failed: %s", e)
            return []

        results: List[DBLog] = []
        for row in raw:
            try:
                context_data = row.get("context", {}).copy()
                row_id = row.get("id", "")
                if "logged_at" in context_data and isinstance(
                    context_data["logged_at"], str
                ):
                    try:
                        context_data["logged_at"] = datetime.fromisoformat(
                            context_data["logged_at"].replace("Z", "+00:00")
                        )
                    except (ValueError, AttributeError):
                        context_data["logged_at"] = datetime.now(timezone.utc)
                entry = DBLog(id=row_id, **context_data)
                await entry.set_context(ctx)
                results.append(entry)
            except Exception as e:
                logger.warning(
                    "change_event_logger: skipping unparseable DBLog row %s: %s",
                    row.get("id"),
                    e,
                )
        return results

    async def get(self, event_id: str) -> Any:
        """Fetch a single DBLog row by id (returns None if absent or wrong level)."""
        from jvspatial.logging.models import DBLog

        ctx = self._get_log_context()
        if ctx is None or not event_id:
            return None
        try:
            entry = await ctx.get(DBLog, event_id)
        except Exception as e:
            logger.warning("change_event_logger: get(%s) failed: %s", event_id, e)
            return None
        if entry is None:
            return None
        if getattr(entry, "log_level", "") != CHANGE_EVENT_LOG_LEVEL:
            return None
        return entry

    async def find_by_staging_token(self, staging_token: str) -> List[Any]:
        """Return CHANGE_EVENT rows whose ``details.staging_token`` matches."""
        from jvspatial.logging.models import DBLog

        if not staging_token:
            return []
        ctx = self._get_log_context()
        if ctx is None:
            return []
        query: Dict[str, Any] = {
            "entity": "DBLog",
            "context.log_level": CHANGE_EVENT_LOG_LEVEL,
            "context.log_data.details.staging_token": staging_token,
        }
        try:
            raw = await ctx.database.find("object", query)
        except Exception as e:
            logger.warning("change_event_logger: find_by_staging_token failed: %s", e)
            return []

        results: List[DBLog] = []
        for row in raw:
            try:
                context_data = row.get("context", {}).copy()
                row_id = row.get("id", "")
                if "logged_at" in context_data and isinstance(
                    context_data["logged_at"], str
                ):
                    try:
                        context_data["logged_at"] = datetime.fromisoformat(
                            context_data["logged_at"].replace("Z", "+00:00")
                        )
                    except (ValueError, AttributeError):
                        context_data["logged_at"] = datetime.now(timezone.utc)
                entry = DBLog(id=row_id, **context_data)
                await entry.set_context(ctx)
                results.append(entry)
            except Exception as e:
                logger.warning(
                    "change_event_logger: skipping unparseable DBLog row %s: %s",
                    row.get("id"),
                    e,
                )
        results.sort(
            key=lambda r: (
                getattr(r, "logged_at", datetime.min.replace(tzinfo=timezone.utc)),
                getattr(r, "id", ""),
            )
        )
        return results

    async def find_after_checkpoint(
        self,
        *,
        last_logged_at: Optional[str] = None,
        last_event_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Any]:
        """Return CHANGE_EVENT rows strictly after ``(last_logged_at, id)``.

        Ordering is ``logged_at`` then ``id``. When ``last_logged_at`` is empty,
        returns the earliest bounded page. Does not advance any checkpoint —
        the work-events consumer owns that.
        """
        from jvspatial.logging.models import DBLog

        if limit <= 0:
            return []
        ctx = self._get_log_context()
        if ctx is None:
            return []
        query: Dict[str, Any] = {
            "entity": "DBLog",
            "context.log_level": CHANGE_EVENT_LOG_LEVEL,
        }
        cursor_logged = (last_logged_at or "").strip()
        cursor_id = (last_event_id or "").strip()
        if cursor_logged:
            # Coarse filter: logged_at >= cursor. Strict after-check below.
            query["context.logged_at"] = {"$gte": cursor_logged}
        try:
            raw = await ctx.database.find("object", query)
        except Exception as e:
            logger.warning("change_event_logger: find_after_checkpoint failed: %s", e)
            return []

        results: List[DBLog] = []
        for row in raw:
            try:
                context_data = row.get("context", {}).copy()
                row_id = row.get("id", "")
                if "logged_at" in context_data and isinstance(
                    context_data["logged_at"], str
                ):
                    try:
                        context_data["logged_at"] = datetime.fromisoformat(
                            context_data["logged_at"].replace("Z", "+00:00")
                        )
                    except (ValueError, AttributeError):
                        context_data["logged_at"] = datetime.now(timezone.utc)
                entry = DBLog(id=row_id, **context_data)
                await entry.set_context(ctx)
                results.append(entry)
            except Exception as e:
                logger.warning(
                    "change_event_logger: skipping unparseable DBLog row %s: %s",
                    row.get("id"),
                    e,
                )

        def _sort_key(r: Any) -> Tuple[datetime, str]:
            logged = getattr(r, "logged_at", None)
            if not isinstance(logged, datetime):
                logged = datetime.min.replace(tzinfo=timezone.utc)
            elif logged.tzinfo is None:
                logged = logged.replace(tzinfo=timezone.utc)
            return (logged, str(getattr(r, "id", "") or ""))

        results.sort(key=_sort_key)
        out: List[DBLog] = []
        for entry in results:
            logged_iso = _sort_key(entry)[0].isoformat()
            eid = str(getattr(entry, "id", "") or "")
            if cursor_logged:
                if logged_iso < cursor_logged:
                    continue
                if logged_iso == cursor_logged and eid <= cursor_id:
                    continue
            out.append(entry)
            if len(out) >= limit:
                break
        return out

    async def patch_event_details(self, event_id: str, patch: Dict[str, Any]) -> bool:
        """Merge ``patch`` into an existing row's ``log_data.details``."""
        row = await self.get(event_id)
        if row is None:
            return False
        data = dict(getattr(row, "log_data", None) or {})
        details = dict(data.get("details") or {})
        details.update(patch)
        data["details"] = details
        row.log_data = data
        await row.save()
        return True


_singleton: Optional[ChangeEventLogger] = None


def get_change_event_logger() -> ChangeEventLogger:
    """Process-wide singleton. ``reset_for_testing()`` drops the cache."""
    global _singleton
    if _singleton is None:
        _singleton = ChangeEventLogger()
    return _singleton


def reset_for_testing() -> None:
    """Drop the cached logger singleton. Test hygiene only."""
    global _singleton
    _singleton = None


__all__ = [
    "CHANGE_EVENT_LOG_LEVEL",
    "ChangeEventEnvelope",
    "ChangeEventLogger",
    "envelope_from_dblog",
    "get_change_event_logger",
    "is_change_event_enabled",
    "reset_for_testing",
]
