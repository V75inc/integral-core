#!/usr/bin/env bash
# Build and import the public Core wheel without a source-tree import path.
#
# This proves packaging boundaries and runtime data files. Dependency resolution
# uses the repository's prepared verification interpreter; a separate clean
# environment with resolved public dependencies remains the C1 release gate.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${ROOT}/backend/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3)"
fi
TMP="$(mktemp -d "${TMPDIR:-/tmp}/integral-artifact.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

# CI has no backend/.venv. The wheel is still installed --no-deps; third-party
# imports (PyYAML, Pydantic) come from this interpreter, not from the checkout.
if ! "$PY" -c 'import yaml, pydantic' >/dev/null 2>&1; then
  uv venv --python "$PY" "$TMP/venv" >/dev/null
  uv export --project "$ROOT/backend" --frozen --no-dev --no-emit-project --no-hashes -o "$TMP/requirements.txt" >/dev/null
  uv pip install --python "$TMP/venv/bin/python" -r "$TMP/requirements.txt" >/dev/null
  PY="$TMP/venv/bin/python"
fi

if [[ -n "${INTEGRAL_WHEEL_PATH:-}" ]]; then
  WHEEL="${INTEGRAL_WHEEL_PATH}"
  test -f "$WHEEL"
else
  uv build "$ROOT/backend" --wheel --out-dir "$TMP/dist" >/dev/null
  WHEEL="$(find "$TMP/dist" -maxdepth 1 -name 'integral_core-*.whl' -print -quit)"
fi
test -n "$WHEEL"
WHEEL_SHA256="$("$PY" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$WHEEL")"
echo "verified-wheel-sha256=$WHEEL_SHA256"
uv pip install --no-deps --target "$TMP/site" "$WHEEL" >/dev/null

# Do not let the current checkout win module resolution. Verify that dynamic
# registries use resources included in the wheel and no internal profile code
# crossed the public-Core boundary.
mkdir "$TMP/run"
(
  cd "$TMP/run"
  export TESTING=1
  export INTEGRAL_CORE_ONLY=1
  export PYTHONPATH="$TMP/site"
  "$PY" - "$TMP/site" "$WHEEL" <<'PYTHON'
import importlib.util
import sys
import zipfile
from pathlib import Path

site = Path(sys.argv[1]).resolve()
wheel = Path(sys.argv[2]).resolve()
# The repository verification venv is editable-installed. Remove its import
# hook so an absent subpackage cannot be fulfilled from the checkout after the
# wheel has supplied the parent ``app`` package.
sys.meta_path[:] = [
    finder
    for finder in sys.meta_path
    if "editable" not in getattr(finder, "__class__", type(finder)).__module__
]
sys.path[:] = [path for path in sys.path if "editable" not in path]
sys.path_importer_cache.clear()
import app
from app.agentive.tooling.manifest import DEFAULT_MANIFEST_PATH
from app.connectors.catalog_loader import load_catalog
from app.views import dashboard_widget_types
from app.views.view_contract_catalog import load_view_contract_catalog

assert str(Path(app.__file__).resolve()).startswith(str(site)), app.__file__
assert DEFAULT_MANIFEST_PATH.is_file(), DEFAULT_MANIFEST_PATH
assert load_catalog(), "connector catalogue missing from wheel"
assert dashboard_widget_types.get_spec("metric_card") is not None
assert load_view_contract_catalog(), "view contracts missing from wheel"
with zipfile.ZipFile(wheel) as archive:
    assert not any(name.startswith("app/packages/") for name in archive.namelist())
print("artifact-wheel-import-ok")
PYTHON
)
