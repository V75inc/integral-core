#!/usr/bin/env bash
# Tool manifest ↔ bindings reconciliation guard (Phase A2).

set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"
# shellcheck source=resolve_python.sh
source "$REPO_ROOT/.ci/resolve_python.sh"

PY="$(resolve_python "$REPO_ROOT")"
cd "$REPO_ROOT/backend"
"$PY" -m pytest tests/test_tool_manifest_reconciliation.py -q
