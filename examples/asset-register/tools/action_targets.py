"""Resolve human-friendly selectors through the current read-only App facade."""

from typing import Any, Dict

from integral_sdk import OperationContext

from .helpers import TRACK_ASSETS, TRACK_CUSTODY, _cf, find_asset_by_tag


async def resolve_targets(
    payload: Dict[str, Any], ctx: OperationContext
) -> Dict[str, str]:
    """Return only exact target IDs; Core validates every returned audience and root."""
    operation = ctx.operation_key
    if operation == "register_asset":
        track_id = await ctx.find_track_id_by_title(TRACK_ASSETS)
        return {"assets_track_id": track_id} if track_id else {}
    asset = None
    if payload.get("asset_id"):
        asset = await ctx.get_entry(str(payload["asset_id"]))
    elif payload.get("asset_tag"):
        asset = await find_asset_by_tag(ctx, str(payload["asset_tag"]))
    if asset is None:
        return {}
    if operation == "check_out_asset":
        custody_track = await ctx.find_track_id_by_title(TRACK_CUSTODY)
        custodian = await ctx.get_entry(str(payload.get("custodian_id") or ""))
        if not custody_track or custodian is None:
            return {}
        return {
            "asset_id": str(asset.id),
            "custodian_id": str(custodian.id),
            "custody_track_id": custody_track,
        }
    if operation == "check_in_asset":
        custody = await ctx.get_entry(str(_cf(asset).get("current_custody") or ""))
        if custody is None:
            return {}
        return {"asset_id": str(asset.id), "custody_id": str(custody.id)}
    return {}
