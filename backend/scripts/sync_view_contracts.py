"""Sync canonical backend view contracts into frontend artifact file."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.views.view_contract_catalog import sync_frontend_contract_catalog


def main() -> None:
    path = sync_frontend_contract_catalog()
    print(f"Synced view contracts to {path}")


if __name__ == "__main__":
    main()
