"""Contract: checkout emits audit trail (AC-10, WP-08)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from app.services.app_operations.context import OperationContext


class _Entry:
    def __init__(self, entry_id: str, title: str = "", custom_fields: Dict[str, Any] | None = None):
        self.id = entry_id
        self.title = title
        self.custom_fields = dict(custom_fields or {})


class _AuditCtx(OperationContext):
    def __init__(self):
        super().__init__(user_id="u1", workspace_id="ws1", scope="ws:ws1")
        self.app_id = "n.App.ar"
        self.operation_key = "check_out_asset"
        self.audits: List[Dict[str, Any]] = []
        self._entries: Dict[str, _Entry] = {}
        self._tracks: Dict[str, str] = {}

    async def get_entry(self, entry_id: str):
        return self._entries.get(entry_id)

    async def update_entry_fields(self, entry_id: str, fields: Dict[str, Any]) -> bool:
        ent = self._entries.get(entry_id)
        if ent is None:
            return False
        ent.custom_fields.update(fields)
        return True

    async def find_track_id_by_title(self, title: str) -> Optional[str]:
        return self._tracks.get(title)

    async def create_entry(self, **kwargs):
        entry = _Entry(
            kwargs.get("entry_id") or f"n.Entry.{len(self._entries) + 1}",
            str(kwargs.get("title") or ""),
            dict(kwargs.get("custom_fields") or {}),
        )
        self._entries[entry.id] = entry
        return entry

    async def find_entries_in_track_type(self, track_title: str, entry_type_key: str):
        return []

    async def emit_audit(self, action: str, details: Dict[str, Any]) -> None:
        self.audits.append({"action": action, "details": details})


@pytest.mark.contract
@pytest.mark.asyncio
async def test_check_out_asset_emits_audit_event():
    repo = Path(__file__).resolve().parents[3]
    bundle_root = str(repo / "examples" / "asset-register")
    sdk = str(repo / "sdk" / "python")
    for p in (sdk, bundle_root):
        if p not in sys.path:
            sys.path.insert(0, p)

    custody = importlib.import_module("tools.custody")

    ctx = _AuditCtx()
    ctx._entries = {
        "n.Entry.asset1": _Entry(
            "n.Entry.asset1",
            "Laptop",
            {"lifecycle_state": "available", "asset_tag": "LT-001"},
        ),
        "n.Entry.cust1": _Entry("n.Entry.cust1", "Alice"),
    }
    ctx._tracks = {"custody": "n.Track.custody"}

    result = await custody.check_out_asset(
        {"asset_id": "n.Entry.asset1", "custodian_id": "n.Entry.cust1"},
        ctx,
    )
    assert result["ok"] is True
    assert any(a["action"] == "asset.check_out" for a in ctx.audits)
    hit = next(a for a in ctx.audits if a["action"] == "asset.check_out")
    assert hit["details"]["asset_id"] == "n.Entry.asset1"
    assert hit["details"]["custodian_id"] == "n.Entry.cust1"
    assert hit["details"]["custody_id"]
