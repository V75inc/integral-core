"""Platform-neutral view contract catalog.

Canonical source lives in ``backend/app/views/contracts/*.json`` so backend and
profile validation remain self-contained and human-discoverable.

Frontend clients consume a synced artifact at ``frontend/src/views/contracts.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

_FALLBACK_CONTRACTS: List[Dict[str, Any]] = [
    {
        "type": "feed",
        "label": "Feed",
        "default_always_on": True,
        "platforms": ["web", "mobile"],
    },
    {
        "type": "kanban",
        "label": "Kanban",
        "default_always_on": False,
        "platforms": ["web", "mobile"],
    },
    {
        "type": "table",
        "label": "Table",
        "default_always_on": False,
        "platforms": ["web", "mobile"],
    },
    {
        "type": "calendar",
        "label": "Calendar",
        "default_always_on": False,
        "platforms": ["web", "mobile"],
    },
    {
        "type": "gallery",
        "label": "Gallery",
        "default_always_on": False,
        "platforms": ["web", "mobile"],
    },
]


def _contracts_dir() -> Path:
    return Path(__file__).resolve().parent / "contracts"


def frontend_contracts_path() -> Path:
    """Return the frontend artifact path for the synced view contract catalog."""
    root = Path(__file__).resolve().parents[3]
    return root / "frontend" / "src" / "views" / "contracts.json"


def _normalize_contract(raw: Dict[str, Any]) -> Dict[str, Any]:
    type_key = str(raw.get("type") or "").strip()
    if not type_key:
        return {}
    return {
        "type": type_key,
        "label": str(raw.get("label") or type_key.title()),
        "default_always_on": bool(raw.get("default_always_on", False)),
        "platforms": [
            str(p).strip() for p in (raw.get("platforms") or []) if str(p).strip()
        ]
        or ["web"],
        "palette_group": str(raw.get("palette_group") or "general"),
        "configurable": bool(raw.get("configurable", True)),
        "hot_loadable": bool(raw.get("hot_loadable", False)),
    }


def _load_contracts_from_backend_dir() -> List[Dict[str, Any]]:
    contracts_dir = _contracts_dir()
    if not contracts_dir.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for path in sorted(contracts_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        normalized = _normalize_contract(data)
        if normalized:
            rows.append(normalized)
    return rows


def load_view_contract_catalog() -> List[Dict[str, Any]]:
    """Load normalized view contracts from ``backend/app/views/contracts``."""
    rows = _load_contracts_from_backend_dir()
    return rows or list(_FALLBACK_CONTRACTS)


def sync_frontend_contract_catalog() -> Path:
    """Write the canonical backend contract list into frontend artifact path."""
    target = frontend_contracts_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = load_view_contract_catalog()
    target.write_text(f"{json.dumps(rows, indent=2)}\n", encoding="utf-8")
    return target
