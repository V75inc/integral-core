#!/usr/bin/env bash
# Keep app/contracts dependency-light so it remains a genuine module boundary.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
PYTHON_BIN="$REPO_ROOT/backend/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="${PYTHON:-python3}"
fi

PYTHONPATH="$REPO_ROOT/backend" "$PYTHON_BIN" - "$REPO_ROOT/backend/app/contracts" <<'PY'
import ast
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
violations = []
for path in sorted(root.rglob("*.py")):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        module = ""
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app.") and not alias.name.startswith("app.contracts"):
                    violations.append(f"{path}: imports private module {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith("app.") and not module.startswith("app.contracts"):
                violations.append(f"{path}: imports private module {module}")

if violations:
    print("contracts boundary check failed:", *violations, sep="\n  ", file=sys.stderr)
    raise SystemExit(1)
print("contracts-boundary: OK")
PY
