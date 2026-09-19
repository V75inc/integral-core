"""Contract: custody state conflicts on double checkout (AC-05 slice, WP-08)."""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from app.services.app_operations.context import OperationContext


class _Entry:
    def __init__(self, entry_id: str, custom_fields: Dict[str, Any]):
        self.id = entry_id
        self.title = "Asset"
        self.custom_fields = dict(custom_fields)


class _CustodyCtx(OperationContext):
    def __init__(self):
        super().__init__(user_id="u1", workspace_id="ws1", scope="ws:ws1")
        self.operation_key = "check_out_asset"
        self._entries: Dict[str, _Entry] = {}
        self._tracks = {"custody": "n.Track.custody"}
        self._lock = asyncio.Lock()

    async def get_entry(self, entry_id: str):
        return self._entries.get(entry_id)

    async def update_entry_fields(self, entry_id: str, fields: Dict[str, Any]) -> bool:
        async with self._lock:
            ent = self._entries.get(entry_id)
            if ent is None:
                return False
            ent.custom_fields.update(fields)
            return True

    async def conditional_update_entry_fields(
        self,
        entry_id: str,
        *,
        state_field: str,
        expected_state: str,
        updates: Dict[str, Any],
    ):
        async with self._lock:
            ent = self._entries.get(entry_id)
            if ent is None:
                return False, "not_found"
            current = str((ent.custom_fields or {}).get(state_field) or "")
            if current != expected_state:
                return False, "state_conflict"
            ent.custom_fields.update(dict(updates or {}))
            after = str((ent.custom_fields or {}).get(state_field) or "")
            intended = str((updates or {}).get(state_field, after))
            if after != intended:
                return False, "state_conflict"
            return True, None

    async def find_track_id_by_title(self, title: str) -> Optional[str]:
        return self._tracks.get(title)

    async def create_entry(self, **kwargs):
        entry = _Entry(
            kwargs.get("entry_id") or f"n.Entry.custody{len(self._entries)}",
            dict(kwargs.get("custom_fields") or {}),
        )
        self._entries[entry.id] = entry
        return entry

    async def find_entries_in_track_type(self, track_title: str, entry_type_key: str):
        return []

    async def emit_audit(self, action: str, details: Dict[str, Any]) -> None:
        return None


@pytest.mark.contract
@pytest.mark.asyncio
async def test_sequential_double_checkout_second_conflicts():
    repo = Path(__file__).resolve().parents[3]
    bundle_root = str(repo / "examples" / "asset-register")
    sdk = str(repo / "sdk" / "python")
    for key in list(sys.modules):
        if key == "tools" or key.startswith("tools."):
            del sys.modules[key]
    for p in (sdk, bundle_root):
        if p not in sys.path:
            sys.path.insert(0, p)

    custody = importlib.import_module("tools.custody")
    helpers = importlib.import_module("tools.helpers")

    ctx = _CustodyCtx()
    ctx._entries = {
        "n.Entry.asset1": _Entry("n.Entry.asset1", {"lifecycle_state": "available"}),
        "n.Entry.cust1": _Entry("n.Entry.cust1", {}),
        "n.Entry.cust2": _Entry("n.Entry.cust2", {}),
    }

    first = await custody.check_out_asset(
        {"asset_id": "n.Entry.asset1", "custodian_id": "n.Entry.cust1"},
        ctx,
    )
    assert first["ok"] is True
    second = await custody.check_out_asset(
        {"asset_id": "n.Entry.asset1", "custodian_id": "n.Entry.cust2"},
        ctx,
    )
    assert second["ok"] is False
    assert second.get("error_code") == "state_conflict"

    asset = await ctx.get_entry("n.Entry.asset1")
    assert asset is not None
    assert asset.custom_fields.get("lifecycle_state") == helpers.STATE_CHECKED_OUT
