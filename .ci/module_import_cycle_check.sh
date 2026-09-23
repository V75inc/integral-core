#!/usr/bin/env bash
# WP-01 — public module contracts and adapters must remain acyclic.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
PYTHON_BIN="$REPO_ROOT/backend/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="${PYTHON:-python3}"
fi

PYTHONPATH="$REPO_ROOT/backend" "$PYTHON_BIN" - "$REPO_ROOT/backend/app" <<'PY'
import ast
import pathlib
import sys

app_root = pathlib.Path(sys.argv[1])
roots = ("contracts", "modules")
files = {}
for root in roots:
    for path in sorted((app_root / root).rglob("*.py")):
        rel = path.relative_to(app_root).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts.pop()
        module = "app." + ".".join(parts)
        files[module] = path

def local_target(name: str) -> str | None:
    if name in files:
        return name
    if name + ".__init__" in files:
        return name + ".__init__"
    return None

edges = {module: set() for module in files}
for module, path in files.items():
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        candidates = []
        if isinstance(node, ast.Import):
            candidates = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            candidates = [node.module]
        for candidate in candidates:
            target = local_target(candidate)
            if target and target != module:
                edges[module].add(target)

visited, visiting, stack, cycles = set(), set(), [], []
def visit(module: str) -> None:
    if module in visiting:
        start = stack.index(module)
        cycles.append(stack[start:] + [module])
        return
    if module in visited:
        return
    visiting.add(module)
    stack.append(module)
    for target in sorted(edges[module]):
        visit(target)
    stack.pop()
    visiting.remove(module)
    visited.add(module)

for module in sorted(edges):
    visit(module)

if cycles:
    print("module import cycle check failed:", file=sys.stderr)
    for cycle in cycles:
        print("  " + " -> ".join(cycle), file=sys.stderr)
    raise SystemExit(1)
print("module-import-cycles: OK")
PY
