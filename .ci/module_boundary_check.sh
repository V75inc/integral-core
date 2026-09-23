#!/usr/bin/env bash
# WP-01 — new modular-monolith adapters may not acquire private Core imports.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
PYTHON_BIN="$REPO_ROOT/backend/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="${PYTHON:-python3}"
fi

PYTHONPATH="$REPO_ROOT/backend" "$PYTHON_BIN" - "$REPO_ROOT/backend/app/modules" <<'PY'
import ast
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
allowed_prefixes = (
    "app.contracts",
    "app.modules",
    "app.schemas",
)
allowed_services = {"app.services.policy_engine"}
forbidden_prefixes = (
    "app.agentive",
    "app.api",
    "app.models",
    "app.plugins",
    "app.packages",
)
violations = []

for path in sorted(root.rglob("*.py")):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    for module in imported:
        if module in allowed_services or module.startswith(allowed_prefixes):
            continue
        if module.startswith(forbidden_prefixes):
            violations.append(f"{path}: forbidden module import {module}")
        elif module.startswith("app.services"):
            violations.append(f"{path}: unapproved legacy service import {module}")

if violations:
    print("module boundary check failed:", *violations, sep="\n  ", file=sys.stderr)
    raise SystemExit(1)
print("module-boundary: OK")
PY
