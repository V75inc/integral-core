"""Atomic conditional custom_fields updates for entry writes (AC-05)."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Tuple

_LOCAL_CAS_LOCKS: Dict[Tuple[int, int, str], asyncio.Lock] = {}


async def conditional_update_entry_custom_fields(
    *,
    user_id: str,
    entry_id: str,
    state_field: str,
    expected_state: str,
    updates: Dict[str, Any],
    scope: str = "",
    event_sink: Any = None,
    schema_revision: Optional[int] = None,
) -> Tuple[bool, Optional[str]]:
    """Merge ``updates`` when ``custom_fields[state_field] == expected_state``.

    Use the active Entry graph context and jvspatial's native atomic update
    implementation. Refuse stores that only inherit the non-atomic base
    implementation; a check-then-save fallback violates this method's contract.
    """
    from app.models.nodes import Entry
    from app.services.permissions import resolve_role

    if not (user_id or "").strip():
        return False, "write_denied"
    role = await resolve_role(user_id, "entry", entry_id)
    if role not in ("owner", "admin", "editor"):
        return False, "write_denied"

    ent = await Entry.get(entry_id)
    if ent is None:
        return False, "not_found"

    merged = dict(updates or {})
    if state_field in merged and str(merged[state_field]) == expected_state:
        return False, "state_conflict"

    from jvspatial.db.database import Database

    graph_context = await ent.get_context()
    db = graph_context.database
    # Wrappers expose ``inner``; inspect the concrete implementation instead
    # of comparing a display class name that breaks as soon as instrumentation
    # or cache layers are enabled.
    concrete = db
    seen = set()
    while getattr(concrete, "inner", None) is not None and id(concrete) not in seen:
        seen.add(id(concrete))
        concrete = concrete.inner
    native_atomic = (
        type(concrete).find_one_and_update is not Database.find_one_and_update
    )
    lock = None
    if not native_atomic:
        # JsonDB and the in-memory test adapter are single-process stores. Their
        # base CAS primitive is a read/modify/write, so serialize this helper's
        # claims per entry and event loop. Multi-process production stores must
        # provide a native atomic implementation; they fail closed above this
        # adapter boundary rather than pretending this lock is distributed.
        if type(concrete).__module__ not in {"jvspatial.db.jsondb", "jvspatial.memory"}:
            return False, "atomic_update_unavailable"
        loop_key = id(asyncio.get_running_loop())
        lock = _LOCAL_CAS_LOCKS.setdefault(
            (id(concrete), loop_key, entry_id), asyncio.Lock()
        )

    collection = graph_context._get_collection_name("n")
    field_path = f"context.custom_fields.{state_field}"
    query = {"id": entry_id, field_path: expected_state}
    set_doc = {f"context.custom_fields.{k}": v for k, v in merged.items()}
    from app.utils.time import utc_now_iso

    set_doc.update(
        {
            "context.record_revision": int(getattr(ent, "record_revision", 1) or 1) + 1,
            "context.updated_at": utc_now_iso(),
        }
    )
    if schema_revision is not None:
        set_doc["context.schema_revision"] = int(schema_revision)
    if lock is None:
        updated = await db.find_one_and_update(collection, query, {"$set": set_doc})
    else:
        async with lock:
            updated = await db.find_one_and_update(collection, query, {"$set": set_doc})
    if updated is None:
        refreshed = await Entry.get(entry_id)
        if refreshed is None:
            return False, "not_found"
        current = str((refreshed.custom_fields or {}).get(state_field) or "")
        if current != expected_state:
            return False, "state_conflict"
        return False, "write_denied"

    before = {k: (ent.custom_fields or {}).get(k) for k in merged}
    existing = (updated.get("context") or {}).get("custom_fields") or {}
    ent.custom_fields = dict(existing)
    ent.record_revision = int(
        (updated.get("context") or {}).get("record_revision")
        or getattr(ent, "record_revision", 1)
        or 1
    )
    ent.updated_at = (updated.get("context") or {}).get("updated_at")
    if schema_revision is not None:
        ent.schema_revision = int(schema_revision)
    event = {
        "actor_kind": "agent",
        "actor_id": user_id,
        "action": "entry.update",
        "resource_type": "Entry",
        "resource_id": entry_id,
        "before": before,
        "after": dict(merged),
        "scope": scope or "entry_conditional_update",
    }
    if event_sink is not None:
        event_sink(event)
    else:
        from app.services.change_event import emit_change_event

        await emit_change_event(**event)
    return True, None


async def update_entry_custom_fields_if_revision(
    *,
    entry: Any,
    expected_revision: int,
    updates: Dict[str, Any],
    schema_revision: int,
    user_id: str,
    scope: str,
    event_sink: Any = None,
) -> Tuple[bool, Optional[str]]:
    """Atomically update a validated Entry snapshot at its observed revision."""
    from jvspatial.db.database import Database

    from app.models.nodes import Entry
    from app.utils.time import utc_now_iso

    if int(getattr(entry, "record_revision", 1) or 1) != int(expected_revision):
        return False, "revision_conflict"
    graph_context = await entry.get_context()
    db = graph_context.database
    concrete = db
    seen: set[int] = set()
    while getattr(concrete, "inner", None) is not None and id(concrete) not in seen:
        seen.add(id(concrete))
        concrete = concrete.inner
    native_atomic = (
        type(concrete).find_one_and_update is not Database.find_one_and_update
    )
    lock = None
    if not native_atomic:
        if type(concrete).__module__ not in {"jvspatial.db.jsondb", "jvspatial.memory"}:
            return False, "atomic_update_unavailable"
        lock = _LOCAL_CAS_LOCKS.setdefault(
            (id(concrete), id(asyncio.get_running_loop()), str(entry.id)),
            asyncio.Lock(),
        )

    collection = graph_context._get_collection_name("n")
    query = {"id": str(entry.id), "context.record_revision": int(expected_revision)}
    now = utc_now_iso()
    replacement = dict(updates or {})
    set_doc = {
        "context.custom_fields": replacement,
        "context.record_revision": int(expected_revision) + 1,
        "context.schema_revision": int(schema_revision),
        "context.updated_at": now,
    }

    async def update():
        return await db.find_one_and_update(collection, query, {"$set": set_doc})

    if lock is None:
        saved = await update()
    else:
        async with lock:
            saved = await update()
    if saved is None:
        current = await Entry.get(str(entry.id))
        return (False, "not_found") if current is None else (False, "revision_conflict")

    before = dict(getattr(entry, "custom_fields", None) or {})
    entry.custom_fields = replacement
    entry.record_revision = int(expected_revision) + 1
    entry.schema_revision = int(schema_revision)
    entry.updated_at = now
    event = {
        "actor_kind": "agent",
        "actor_id": user_id,
        "action": "entry.update",
        "resource_type": "Entry",
        "resource_id": str(entry.id),
        "before": before,
        "after": replacement,
        "scope": scope,
    }
    if event_sink is not None:
        event_sink(event)
    else:
        from app.services.change_event import emit_change_event

        await emit_change_event(**event)
    return True, None
