#!/usr/bin/env bash
# Prove the public boundary without relying on Core's source tree: build both
# public wheels, install them into a fresh environment, copy Asset Register to
# an unrelated directory, then load and resolve its declared handler through
# Core's external-package contract. The App is built into an archive before
# installation; copying the checkout would let untracked files and source-path
# imports hide a packaging defect.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/integral-external-asset-register.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

run_logged() {
  local log="$1"
  shift
  if ! "$@" >"$log" 2>&1; then
    cat "$log" >&2
    return 1
  fi
}

run_logged "$TMP/core-build.log" uv build "$ROOT/backend" --wheel --out-dir "$TMP/dist"
run_logged "$TMP/sdk-build.log" uv build "$ROOT/sdk/python" --wheel --out-dir "$TMP/dist"
CORE_WHEEL="$(find "$TMP/dist" -maxdepth 1 -name 'integral_core-*.whl' -print -quit)"
SDK_WHEEL="$(find "$TMP/dist" -maxdepth 1 -name 'integral_sdk-*.whl' -print -quit)"
test -n "$CORE_WHEEL"
test -n "$SDK_WHEEL"
run_logged "$TMP/venv.log" uv venv "$TMP/venv"
run_logged "$TMP/install-sdk.log" uv pip install --python "$TMP/venv/bin/python" "$SDK_WHEEL"
run_logged "$TMP/install-core.log" uv pip install --python "$TMP/venv/bin/python" "$CORE_WHEEL"

mkdir -p "$TMP/extensions"
run_logged "$TMP/package-build.log" python3 "$ROOT/examples/asset-register/build.py" --out-dir "$TMP/packages"
ARCHIVE="$(find "$TMP/packages" -maxdepth 1 -name 'asset-register-*.tar.gz' -print -quit)"
test -n "$ARCHIVE"
EXPECTED_DIGEST="$(awk '{print $1}' "${ARCHIVE}.sha256")"
test "$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')" = "$EXPECTED_DIGEST"
run_logged "$TMP/package-extract.log" tar -xzf "$ARCHIVE" -C "$TMP/extensions"
mkdir "$TMP/run"
(
  cd "$TMP/run"
  INTEGRAL_CORE_ONLY=0 "$TMP/venv/bin/python" - "$TMP/extensions" <<'PYTHON'
import asyncio
import sys
from pathlib import Path

from app.services.content_profile_loader import load_library_profiles_with_issues
from app.services.content_profile_runtime import compile_canonical_manifest
from app.services.hooks.install_hook import _normalize_handler_ref, register_bundle_on_install
from app.services.hooks.registry import get_workspace_tools
from app.services.hooks.tool_dispatch import resolve_handler

extension_root = Path(sys.argv[1]).resolve()
specs, issues = load_library_profiles_with_issues(
    package_paths=[str(extension_root)], core_only=False, verify_signatures=False
)
assert not issues, issues
spec = next(spec for spec in specs if spec.slug == "asset-register")
assert spec.bundle_dir is not None
assert str(spec.bundle_dir.resolve()).startswith(str(extension_root)), spec.bundle_dir
canonical = compile_canonical_manifest(manifest=spec.manifest)
asyncio.run(
    register_bundle_on_install(
        "ws-external-asset-register", canonical, bundle_dir=str(spec.bundle_dir)
    )
)
handler_ref = _normalize_handler_ref(
    spec.slug, "tools.assets:register_asset", bundle_dir=str(spec.bundle_dir)
)
handler = resolve_handler(handler_ref)
assert callable(handler)
handler_module = sys.modules[handler.__module__]
handler_path = Path(handler_module.__file__).resolve()
assert str(handler_path).startswith(str(extension_root)), handler_path
assert "register_asset" in get_workspace_tools("ws-external-asset-register")
print("external-asset-register-load-ok")
PYTHON
)
