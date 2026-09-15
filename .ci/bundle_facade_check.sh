#!/usr/bin/env bash
# Bundle tool facade guard — App package tools must not import substrate internals.
#
# Tools live under packages/apps/*/tools/ (commercial) and any remaining
# Core seed tools under backend/app/profiles/*/tools/.

set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"

if ! command -v grep >/dev/null 2>&1; then
  echo "bundle-facade: FATAL — grep not found on PATH; cannot enforce I-HOOK-01." >&2
  exit 1
fi

shopt -s nullglob
TOOL_FILES=(
  "$REPO_ROOT"/packages/apps/*/tools/*.py
  "$REPO_ROOT"/backend/app/profiles/*/tools/*.py
)
if [ ${#TOOL_FILES[@]} -eq 0 ]; then
  echo "bundle-facade: no bundle tools to check."
  exit 0
fi

# ToolContext lives under app.services.hooks.registry; that one import *is*
# the facade (I-HOOK-02). Everything else from app.services / app.models is
# still forbidden.
#
# grep exits 1 on "no matches", which is the clean case, so `|| true` is
# intentional for the scan itself — not for missing grep (handled above).
VIOLATIONS=""
for f in "${TOOL_FILES[@]}"; do
  hits=$(grep -nE 'from app\.(services|models)\.|import app\.(services|models)' "$f" 2>/dev/null \
    | grep -v 'app\.services\.hooks\.registry' || true)
  if [ -n "$hits" ]; then
    VIOLATIONS+="$f"$'\n'"$hits"$'\n'
  fi
done

if [ -n "$VIOLATIONS" ]; then
  echo "bundle-facade: FAIL — tools must not import app.services/models directly:" >&2
  echo "$VIOLATIONS" >&2
  exit 1
fi

echo "bundle-facade: clean (${#TOOL_FILES[@]} bundle tool file(s) checked)."
