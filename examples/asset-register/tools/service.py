"""Service history tools."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from integral_sdk import OperationContext

from .helpers import (
    ENTRY_ASSET,
    ENTRY_SERVICE,
    TRACK_ASSETS,
    TRACK_SERVICE_HISTORY,
    create_track_entry,
    find_asset_by_tag,
)


async def record_service(input: Dict[str, Any], ctx: OperationContext) -> Dict[str, Any]:
    """Append a service record linked to an asset."""
    payload = dict(input or {})
    asset_id = str(payload.get("asset_id") or "").strip()
    asset_tag = str(payload.get("asset_tag") or "").strip()
    summary = str(payload.get("summary") or "").strip()
    service_date = payload.get("service_date")

    asset = None
    if asset_id:
        asset = await ctx.get_entry(asset_id)
    elif asset_tag:
        asset = await find_asset_by_tag(ctx, asset_tag)
    if asset is None:
        return {"ok": False, "error_code": "not_found", "message": "asset not found"}
    if not summary:
        return {"ok": False, "error_code": "invalid_input", "message": "summary required"}

    title = f"Service — {getattr(asset, 'title', asset.id)}"
    custom_fields = {
        "asset": asset.id,
        "service_date": service_date or datetime.utcnow().date().isoformat(),
        "summary": summary,
        "provider": str(payload.get("provider") or "").strip(),
        "next_service_date": payload.get("next_service_date"),
    }
    created, err = await create_track_entry(
        ctx,
        track_title=TRACK_SERVICE_HISTORY,
        entry_type_key=ENTRY_SERVICE,
        title=title,
        custom_fields=custom_fields,
        body=str(payload.get("notes") or ""),
    )
    if err:
        return {"ok": False, "error_code": err, "message": "service record not created"}

    if payload.get("next_service_date"):
        await ctx.update_entry_fields(
            asset.id,
            {"next_service_date": payload.get("next_service_date")},
        )

    return {
        "ok": True,
        "service_id": getattr(created, "id", ""),
        "asset_id": asset.id,
    }
