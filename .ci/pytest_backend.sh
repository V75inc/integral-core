#!/usr/bin/env bash
# Run backend pytest for the manual pre-commit pytest-backend hook.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=resolve_python.sh
source "$REPO_ROOT/.ci/resolve_python.sh"

PY="$(resolve_python "$REPO_ROOT")"
cd "$REPO_ROOT/backend"

exec "$PY" -m pytest tests/ -q --tb=short
