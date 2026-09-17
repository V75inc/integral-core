"""Shared helpers for Asset Register tools — ToolContext facade only."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from integral_sdk import OperationContext

STATE_AVAILABLE = "available"
STATE_CHECKED_OUT = "checked_out"
STATE_MAINTENANCE = "maintenance"
STATE_RETIRED = "retired"

TRACK_ASSETS = "assets"
TRACK_LOCATIONS = "locations"
TRACK_CUSTODIANS = "custodians"
TRACK_CUSTODY = "custody"
TRACK_SERVICE_HISTORY = "service_history"

ENTRY_ASSET = "asset"
ENTRY_LOCATION = "location"
ENTRY_CUSTODIAN = "custodian"
ENTRY_CUSTODY = "custody_record"
ENTRY_SERVICE = "service_record"


def _cf(entry: Any) -> Dict[str, Any]:
    return dict(getattr(entry, "custom_fields", None) or {})


def normalize_tag(tag: str) -> str:
    return str(tag or "").strip().upper()


async def find_asset_by_tag(ctx: OperationContext, asset_tag: str) -> Optional[Any]:
    want = normalize_tag(asset_tag)
    if not want:
        return None
    for entry in await ctx.find_entries_in_track_type(TRACK_ASSETS, ENTRY_ASSET):
        if normalize_tag(_cf(entry).get("asset_tag")) == want:
            return entry
    return None


async def conditional_update_fields(
    ctx: OperationContext,
    entry_id: str,
    *,
    state_field: str,
    expected_state: str,
    updates: Dict[str, Any],
) -> Tuple[bool, Optional[str]]:
    """Transition entry state only when ``state_field`` equals ``expected_state``.

    Uses canonical ``get_entry`` + ``update_entry_fields`` writes. Returns
    ``(True, None)`` on success; ``(False, error_code)`` on conflict, denial,
    or missing entry.
    """
    merged = {**updates}
    if state_field in merged and str(merged[state_field]) == expected_state:
        return False, "state_conflict"
    atomic = getattr(ctx, "conditional_update_entry_fields", None)
    if callable(atomic):
        return await atomic(
            entry_id,
            state_field=state_field,
            expected_state=expected_state,
            updates=merged,
        )
    entry = await ctx.get_entry(entry_id)
    if entry is None:
        return False, "not_found"
    current = str(_cf(entry).get(state_field) or "")
    if current != expected_state:
        return False, "state_conflict"
    if not await ctx.update_entry_fields(entry_id, merged):
        return False, "write_denied"
    refreshed = await ctx.get_entry(entry_id)
    if refreshed is None:
        return False, "not_found"
    after = str(_cf(refreshed).get(state_field) or "")
    intended = str(merged.get(state_field, after))
    if after != intended:
        return False, "state_conflict"
    return True, None


async def create_track_entry(
    ctx: OperationContext,
    *,
    track_title: str,
    entry_type_key: str,
    title: str,
    custom_fields: Optional[Dict[str, Any]] = None,
    body: str = "",
) -> Tuple[Optional[Any], Optional[str]]:
    """Create an entry via ``ToolContext.create_entry`` when available."""
    track_id = await ctx.find_track_id_by_title(track_title)
    if not track_id:
        return None, "track_not_found"
    create = getattr(ctx, "create_entry", None)
    if not callable(create):
        return None, "create_entry_unavailable"
    created = await create(
        track_id=track_id,
        entry_type_key=entry_type_key,
        title=title,
        custom_fields=dict(custom_fields or {}),
        body=body,
    )
    if created is None:
        return None, "create_failed"
    return created, None
