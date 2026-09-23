#!/usr/bin/env bash
# Build the distributable Core wheel, install it with its public metadata into
# a new virtual environment, then import the ASGI application outside this
# checkout. This is deliberately a release/developer-proof lane: it resolves
# production dependencies from the network and therefore is not folded into
# the fast local ``make verify`` gate.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/integral-clean-install.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

run_logged() {
  local log="$1"
  shift
  if ! "$@" >"$log" 2>&1; then
    cat "$log" >&2
    return 1
  fi
}

run_logged "$TMP/build.log" uv build "$ROOT/backend" --wheel --out-dir "$TMP/dist"
WHEEL="$(find "$TMP/dist" -maxdepth 1 -name 'integral_core-*.whl' -print -quit)"
test -n "$WHEEL"
run_logged "$TMP/venv.log" uv venv "$TMP/venv"
run_logged "$TMP/install.log" "$ROOT/.ci/install_core_wheel.sh" "$TMP/venv/bin/python" "$WHEEL"

# Work from a directory without the checkout on sys.path and force a local
# SQLite configuration so application construction exercises the distributable
# ASGI surface without requiring a running service.
mkdir "$TMP/run"
(
  cd "$TMP/run"
  DEBUG=true \
  INTEGRAL_CORE_ONLY=1 \
  JVSPATIAL_DB_TYPE=sqlite \
  JVSPATIAL_DB_PATH="$TMP/integral.db" \
  JVSPATIAL_JWT_SECRET_KEY='0123456789abcdef0123456789abcdef' \
  "$TMP/venv/bin/python" - <<'PYTHON'
import app
from app.main import app as asgi_app

assert "/site-packages/app/" in app.__file__, app.__file__
assert asgi_app.title == "Integral API", asgi_app.title
print("clean-install-asgi-import-ok")
PYTHON
)
