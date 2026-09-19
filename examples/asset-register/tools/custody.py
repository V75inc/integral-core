"""Custody mutation tools — check-out and check-in."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Dict

from integral_sdk import OperationContext

from .helpers import (
    ENTRY_ASSET,
    ENTRY_CUSTODY,
    STATE_AVAILABLE,
    STATE_CHECKED_OUT,
    TRACK_ASSETS,
    TRACK_CUSTODY,
    _cf,
    conditional_update_fields,
    create_track_entry,
    find_asset_by_tag,
)


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


async def check_out_asset(input: Dict[str, Any], ctx: OperationContext) -> Dict[str, Any]:
    """Atomically open custody when asset lifecycle_state is available."""
    payload = dict(input or {})
    asset_id = str(payload.get("asset_id") or "").strip()
    asset_tag = str(payload.get("asset_tag") or "").strip()
    custodian_id = str(payload.get("custodian_id") or "").strip()
    expected_return = payload.get("expected_return")

    asset = None
    if asset_id:
        asset = await ctx.get_entry(asset_id)
    elif asset_tag:
        asset = await find_asset_by_tag(ctx, asset_tag)
    if asset is None:
        return {"ok": False, "error_code": "not_found", "message": "asset not found"}
    if not custodian_id:
        return {
            "ok": False,
            "error_code": "invalid_input",
            "message": "custodian_id required",
        }
    custodian = await ctx.get_entry(custodian_id)
    if custodian is None:
        return {"ok": False, "error_code": "not_found", "message": "custodian not found"}

    ok, err = await conditional_update_fields(
        ctx,
        asset.id,
        state_field="lifecycle_state",
        expected_state=STATE_AVAILABLE,
        updates={
            "lifecycle_state": STATE_CHECKED_OUT,
            "current_custodian": custodian_id,
        },
    )
    if not ok:
        return {
            "ok": False,
            "error_code": err or "state_conflict",
            "message": "asset is not available for checkout",
            "asset_id": asset.id,
        }

    custody_fields = {
        "asset": asset.id,
        "custodian": custodian_id,
        "checked_out_at": _now_iso(),
        "expected_return": expected_return,
        "returned_at": "",
        "condition_out": str(payload.get("condition_notes") or "").strip(),
        "operation_key": getattr(ctx, "operation_key", "") or "check_out_asset",
        "correlation_id": getattr(ctx, "correlation_id", None),
    }
    custody_title = f"Custody — {getattr(asset, 'title', asset.id)}"
    custody_entry, create_err = await create_track_entry(
        ctx,
        track_title=TRACK_CUSTODY,
        entry_type_key=ENTRY_CUSTODY,
        title=custody_title,
        custom_fields=custody_fields,
    )
    if create_err:
        await ctx.update_entry_fields(
            asset.id,
            {
                "lifecycle_state": STATE_AVAILABLE,
                "current_custodian": "",
            },
        )
        return {"ok": False, "error_code": create_err, "message": "custody record not created"}

    await ctx.update_entry_fields(
        asset.id,
        {"current_custody": getattr(custody_entry, "id", "")},
    )

    await ctx.emit_audit(
        "asset.check_out",
        {
            "asset_id": asset.id,
            "custodian_id": custodian_id,
            "custody_id": getattr(custody_entry, "id", ""),
        },
    )
    return {
        "ok": True,
        "asset_id": asset.id,
        "custody_id": getattr(custody_entry, "id", ""),
        "lifecycle_state": STATE_CHECKED_OUT,
    }


async def check_in_asset(input: Dict[str, Any], ctx: OperationContext) -> Dict[str, Any]:
    """Close active custody and return asset to available."""
    payload = dict(input or {})
    asset_id = str(payload.get("asset_id") or "").strip()
    asset_tag = str(payload.get("asset_tag") or "").strip()
    condition_in = str(payload.get("condition_notes") or "").strip()

    asset = None
    if asset_id:
        asset = await ctx.get_entry(asset_id)
    elif asset_tag:
        asset = await find_asset_by_tag(ctx, asset_tag)
    if asset is None:
        return {"ok": False, "error_code": "not_found", "message": "asset not found"}

    cf = _cf(asset)
    if str(cf.get("lifecycle_state") or "") != STATE_CHECKED_OUT:
        return {
            "ok": False,
            "error_code": "state_conflict",
            "message": "asset is not checked out",
            "asset_id": asset.id,
        }

    custody_id = str(cf.get("current_custody") or "")
    if custody_id:
        await ctx.update_entry_fields(
            custody_id,
            {
                "returned_at": _now_iso(),
                "condition_in": condition_in,
            },
        )

    ok, err = await conditional_update_fields(
        ctx,
        asset.id,
        state_field="lifecycle_state",
        expected_state=STATE_CHECKED_OUT,
        updates={
            "lifecycle_state": STATE_AVAILABLE,
            "current_custodian": "",
            "current_custody": "",
        },
    )
    if not ok:
        return {
            "ok": False,
            "error_code": err or "state_conflict",
            "message": "check-in failed",
            "asset_id": asset.id,
        }

    await ctx.emit_audit(
        "asset.check_in",
        {"asset_id": asset.id, "custody_id": custody_id, "condition_in": condition_in},
    )
    return {
        "ok": True,
        "asset_id": asset.id,
        "custody_id": custody_id,
        "lifecycle_state": STATE_AVAILABLE,
    }
