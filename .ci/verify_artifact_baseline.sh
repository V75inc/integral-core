#!/usr/bin/env bash
# WP-01: verify Core boots from built artifact path without commercial tree on PYTHONPATH.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
PY="${ROOT}/backend/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3)"
fi
export TESTING=1
export INTEGRAL_CORE_ONLY=1
unset PYTHONPATH
"$PY" -c "import app.config; import app.services.app_operations.dispatch; print('artifact-import-ok')"
