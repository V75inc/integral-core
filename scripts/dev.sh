#!/usr/bin/env bash
# Run the native Integral API from the frozen source environment.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
uv sync --frozen --extra dev --extra test
exec .venv/bin/python -m app.main
