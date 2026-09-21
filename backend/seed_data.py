"""Deprecated entrypoint — product dogfood seed lives at scripts/seed_product.py.

Requires packages/apps/ (commercial monorepo). Core extract has no domain seed.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_APPS = _REPO / "packages" / "apps"
_SCRIPT = _REPO / "scripts" / "seed_product.py"

if not _APPS.is_dir():
    sys.stderr.write(
        "seed_data: packages/apps/ missing — Core-only tree has no product seed.\n"
        "Use scripts/seed_product.py from the commercial monorepo.\n"
    )
    sys.exit(2)

# Ensure app.packages.* resolves commercial packages before the seed imports.
sys.path.insert(0, str(_REPO / "backend"))
runpy.run_path(str(_SCRIPT), run_name="__main__")
