"""Atomic conditional custom_fields updates for entry writes (AC-05)."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

_NODE_COLLECTION = "n"


async def conditional_update_entry_custom_fields(
    *,
    user_id: str,
    entry_id: str,
    state_field: str,
    expected_state: str,
    updates: Dict[str, Any],
    scope: str = "",
) -> Tuple[bool, Optional[str]]:
    """Merge ``updates`` when ``custom_fields[state_field] == expected_state``.

    On Postgres uses ``find_one_and_update`` for row-level atomicity. Other
    backends fall back to read-modify-write with the same permission checks.
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

    from jvspatial.db import get_prime_database

    db = get_prime_database()
    db_type = type(db).__name__

    if db_type == "PostgresDB":
        field_path = f"custom_fields.{state_field}"
        query = {"id": entry_id, field_path: expected_state}
        set_doc = {f"custom_fields.{k}": v for k, v in merged.items()}
        updated = await db.find_one_and_update(
            _NODE_COLLECTION,
            query,
            {"$set": set_doc},
        )
        if updated is None:
            refreshed = await Entry.get(entry_id)
            if refreshed is None:
                return False, "not_found"
            current = str((refreshed.custom_fields or {}).get(state_field) or "")
            if current != expected_state:
                return False, "state_conflict"
            return False, "write_denied"
        after = str((updated.get("custom_fields") or {}).get(state_field) or "")
        intended = str(merged.get(state_field, after))
        if after != intended:
            return False, "state_conflict"
        before = {
            k: (ent.custom_fields or {}).get(k) for k in merged if k in (ent.custom_fields or {})
        }
        from app.services.change_event import emit_change_event

        await emit_change_event(
            actor_kind="agent",
            actor_id=user_id,
            action="entry.update",
            resource_type="Entry",
            resource_id=entry_id,
            before=before,
            after=dict(merged),
            scope=scope or "entry_conditional_update",
        )
        return True, None

    existing = ent.custom_fields or {}
    current = str(existing.get(state_field) or "")
    if current != expected_state:
        return False, "state_conflict"
    ent.custom_fields = {**existing, **merged}
    await ent.save()

    from app.services.change_event import emit_change_event

    before = {k: existing.get(k) for k in merged}
    await emit_change_event(
        actor_kind="agent",
        actor_id=user_id,
        action="entry.update",
        resource_type="Entry",
        resource_id=entry_id,
        before=before,
        after=dict(merged),
        scope=scope or "entry_conditional_update",
    )
    return True, None
