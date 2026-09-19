"""Asset catalog tools — list, register, warranty review."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List

from integral_sdk import OperationContext

from .helpers import (
    ENTRY_ASSET,
    STATE_AVAILABLE,
    TRACK_ASSETS,
    _cf,
    create_track_entry,
    find_asset_by_tag,
    normalize_tag,
)


def _parse_horizon_days(raw: Any, default: int = 30) -> int:
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(val, 365))


def _asset_summary(entry: Any) -> Dict[str, Any]:
    cf = _cf(entry)
    return {
        "entry_id": entry.id,
        "title": getattr(entry, "title", "") or "",
        "asset_tag": cf.get("asset_tag"),
        "category": cf.get("category"),
        "lifecycle_state": cf.get("lifecycle_state"),
        "location_id": cf.get("location"),
        "warranty_end": cf.get("warranty_end"),
    }


async def list_available_assets(
    input: Dict[str, Any], ctx: OperationContext
) -> Dict[str, Any]:
    """Paginated availability listing with optional filters."""
    category = str((input or {}).get("category") or "").strip().lower()
    location_id = str((input or {}).get("location_id") or "").strip()
    limit = int((input or {}).get("limit") or 50)
    limit = max(1, min(limit, 200))
    offset = max(0, int((input or {}).get("offset") or 0))

    rows: List[Any] = []
    for entry in await ctx.find_entries_in_track_type(TRACK_ASSETS, ENTRY_ASSET):
        cf = _cf(entry)
        if str(cf.get("lifecycle_state") or "") != STATE_AVAILABLE:
            continue
        if category and str(cf.get("category") or "").lower() != category:
            continue
        if location_id and str(cf.get("location") or "") != location_id:
            continue
        rows.append(entry)

    rows.sort(key=lambda e: normalize_tag(_cf(e).get("asset_tag")))
    page = rows[offset : offset + limit]
    return {
        "ok": True,
        "total": len(rows),
        "offset": offset,
        "limit": limit,
        "assets": [_asset_summary(e) for e in page],
    }


async def register_asset(input: Dict[str, Any], ctx: OperationContext) -> Dict[str, Any]:
    """Create a governed asset row with normalized tag uniqueness."""
    payload = dict(input or {})
    asset_tag = normalize_tag(payload.get("asset_tag"))
    title = str(payload.get("title") or "").strip()
    if not asset_tag:
        return {"ok": False, "error_code": "invalid_input", "message": "asset_tag required"}
    if not title:
        return {"ok": False, "error_code": "invalid_input", "message": "title required"}

    if await find_asset_by_tag(ctx, asset_tag):
        return {
            "ok": False,
            "error_code": "duplicate_tag",
            "message": f"asset tag {asset_tag!r} already exists",
        }

    custom_fields: Dict[str, Any] = {
        "asset_tag": asset_tag,
        "category": str(payload.get("category") or "other").strip().lower() or "other",
        "serial_number": str(payload.get("serial_number") or "").strip(),
        "purchase_date": payload.get("purchase_date"),
        "purchase_amount": payload.get("purchase_amount"),
        "currency": str(payload.get("currency") or "USD").strip() or "USD",
        "lifecycle_state": STATE_AVAILABLE,
        "warranty_start": payload.get("warranty_start"),
        "warranty_end": payload.get("warranty_end"),
        "warranty_provider": str(payload.get("warranty_provider") or "").strip(),
    }
    if payload.get("location_id"):
        custom_fields["location"] = str(payload.get("location_id"))

    created, err = await create_track_entry(
        ctx,
        track_title=TRACK_ASSETS,
        entry_type_key=ENTRY_ASSET,
        title=title,
        custom_fields=custom_fields,
        body=str(payload.get("notes") or ""),
    )
    if err:
        return {"ok": False, "error_code": err, "message": "could not create asset"}
    return {"ok": True, "asset": _asset_summary(created)}


async def review_warranties(
    input: Dict[str, Any], ctx: OperationContext
) -> Dict[str, Any]:
    """List assets with warranty expiring within the configured horizon."""
    horizon = _parse_horizon_days((input or {}).get("horizon_days"))
    today = date.today()
    cutoff = today + timedelta(days=horizon)
    expiring: List[Dict[str, Any]] = []

    for entry in await ctx.find_entries_in_track_type(TRACK_ASSETS, ENTRY_ASSET):
        cf = _cf(entry)
        raw_end = cf.get("warranty_end")
        if not raw_end:
            continue
        try:
            end = date.fromisoformat(str(raw_end)[:10])
        except ValueError:
            continue
        if today <= end <= cutoff:
            item = _asset_summary(entry)
            item["days_until_expiry"] = (end - today).days
            expiring.append(item)

    expiring.sort(key=lambda row: (row.get("days_until_expiry", 9999), row.get("asset_tag") or ""))
    return {
        "ok": True,
        "horizon_days": horizon,
        "reviewed_at": datetime.utcnow().isoformat() + "Z",
        "expiring_assets": expiring,
        "count": len(expiring),
    }
