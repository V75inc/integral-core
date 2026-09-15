from __future__ import annotations

import json

from app.views.view_contract_catalog import (
    frontend_contracts_path,
    load_view_contract_catalog,
    sync_frontend_contract_catalog,
)


def test_load_view_contract_catalog_uses_backend_contract_files() -> None:
    rows = load_view_contract_catalog()
    keys = {str(r.get("type")) for r in rows}
    assert "feed" in keys
    assert "kanban" in keys
    assert "composable_timeline" in keys


def test_sync_frontend_contract_catalog_writes_backend_canonical_rows() -> None:
    rows = load_view_contract_catalog()
    target = sync_frontend_contract_catalog()
    assert target == frontend_contracts_path()
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved == rows
