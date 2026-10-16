#!/usr/bin/env bash
# Build and import the public SDK from a fresh virtual environment. This proves
# App authors can consume its contract without the Integral Core checkout.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/.ci/reproducible_build_env.sh"
integral_set_source_date_epoch "$ROOT"
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
import sys
from typing import get_args

import integral_sdk
from integral_sdk import (
    AvailableOperationResponse, FieldDefinition, OperationContext,
    OperationResponse, RecordRevision, ToolContextV2, WithheldOperationResponse,
)

assert "/site-packages/integral_sdk/" in integral_sdk.__file__, integral_sdk.__file__
assert integral_sdk.__version__ == "0.2.0"
assert FieldDefinition and RecordRevision and OperationContext and ToolContextV2
assert AvailableOperationResponse.__required_keys__ == {"app_id", "operation_key", "output"}
assert WithheldOperationResponse.__required_keys__ == {
    "operation_receipt", "output_disposition", "current_state_verified",
}
assert "output" not in WithheldOperationResponse.__annotations__
assert set(get_args(OperationResponse)) == {AvailableOperationResponse, WithheldOperationResponse}
assert not any(name == "app" or name.startswith("app.") for name in sys.modules)
print("sdk-wheel-import-ok")
PYTHON
)
