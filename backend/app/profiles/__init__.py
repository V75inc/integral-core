"""Content Profile bundle namespace.

Core seeds live as subdirectories here (``personal-context``, ``agent-scratch``).
Commercial Apps live under ``packages/apps/`` (not in the Core extract tree).

When ``packages/apps`` exists, it is appended to this package's ``__path__`` so
legacy ``from app.profiles.<alias>…`` imports (underscore shims) keep resolving
in the commercial monorepo without vendoring domain code into Core.
"""

from __future__ import annotations

from pathlib import Path

# Repo root: backend/app/profiles → parents[3] = repo
_APPS_ROOT = Path(__file__).resolve().parents[3] / "packages" / "apps"
if _APPS_ROOT.is_dir():
    __path__.append(str(_APPS_ROOT))  # type: ignore[name-defined]
