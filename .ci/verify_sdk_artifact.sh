#!/usr/bin/env bash
# Build and import the public SDK from a fresh virtual environment. This proves
# App authors can consume its contract without the Integral Core checkout.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/integral-sdk-artifact.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

run_logged() {
  local log="$1"
  shift
  if ! "$@" >"$log" 2>&1; then
    cat "$log" >&2
    return 1
  fi
}

run_logged "$TMP/build.log" uv build "$ROOT/sdk/python" --wheel --out-dir "$TMP/dist"
WHEEL="$(find "$TMP/dist" -maxdepth 1 -name 'integral_sdk-*.whl' -print -quit)"
test -n "$WHEEL"
run_logged "$TMP/venv.log" uv venv "$TMP/venv"
run_logged "$TMP/install.log" uv pip install --python "$TMP/venv/bin/python" "$WHEEL"

mkdir "$TMP/run"
(
  cd "$TMP/run"
  "$TMP/venv/bin/python" - <<'PYTHON'
import integral_sdk
from integral_sdk import FieldDefinition, OperationContext, RecordRevision, ToolContextV2

assert "/site-packages/integral_sdk/" in integral_sdk.__file__, integral_sdk.__file__
assert integral_sdk.__version__ == "0.2.0"
assert FieldDefinition and RecordRevision and OperationContext and ToolContextV2
print("sdk-wheel-import-ok")
PYTHON
)
