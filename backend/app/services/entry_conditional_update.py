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
    expected_state: Optional[str],
    updates: Dict[str, Any],
    scope: str = "",
    event_sink: Any = None,
    schema_revision: Optional[int] = None,
    actor_kind: str = "human",
    actor_id: str | None = None,
) -> Tuple[bool, Optional[str]]:
    """Use the canonical guarded command at an exact observed state/revision."""
    from app.api.errors import InsufficientPermissionsError, ResourceConflictError
    from app.models.nodes import Entry, Track
    from app.services.entry_update import update_entry_in_track
    from app.services.entry_write_scope import entry_write_scope

    if (
        not user_id
        or not isinstance(state_field, str)
        or not state_field
        or "." in state_field
        or "$" in state_field
    ):
        return False, "write_denied"
    try:
        async with entry_write_scope(entry_id):
            current = await Entry.find_one({"id": entry_id})
            if current is None:
                return False, "not_found"
            track = await Track.find_one({"id": current.track_id})
            if track is None:
                return False, "not_found"
            from app.services.entry_effect_scope import entry_effect_scope

            async with entry_effect_scope(
                user_id=user_id,
                actor_kind=actor_kind,
                actor_id=actor_id,
                action="entry.update",
                target_kind="entry",
                target_id=current.id,
                workspace_id=track.workspace_id,
                payload={"entry_id": entry_id, "custom_fields": dict(updates or {})},
            ):
                if (current.custom_fields or {}).get(state_field) != expected_state:
                    return False, "state_conflict"
                await update_entry_in_track(
                    entry_id=entry_id,
                    user_id=user_id,
                    workspace_id=track.workspace_id,
                    actor_kind=actor_kind,
                    actor_id=actor_id,
                    custom_fields=dict(updates or {}),
                    expected_record_revision=current.record_revision,
                    expected_schema_revision=schema_revision,
                    change_event_sink=event_sink,
                )
                return True, None
    except InsufficientPermissionsError:
        return False, "write_denied"
    except ResourceConflictError:
        return False, "state_conflict"


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
