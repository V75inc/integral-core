#!/usr/bin/env bash
# jvspatial-convention drift guard.
#
# Fails if raw FastAPI patterns appear in backend/app/ outside:
#   - backend/app/main.py (legitimate Server bootstrap)
#   - files listed in .ci/jvspatial_drift_allowlist.txt (known debt)
#   - lines marked with an inline '# deviation:' comment
#
# See AGENTS.md § jvspatial Object-Spatial Contract for the conventions enforced.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ALLOWLIST="$REPO_ROOT/.ci/jvspatial_drift_allowlist.txt"

cd "$REPO_ROOT"

# Build a grep -F pattern file with comments/blanks stripped from the allowlist.
ALLOWLIST_TMP="$(mktemp)"
trap 'rm -f "$ALLOWLIST_TMP"' EXIT
grep -v '^\s*#' "$ALLOWLIST" | grep -v '^\s*$' > "$ALLOWLIST_TMP" || true

# Forbidden patterns (hard-forbidden per AGENTS.md § Forbidden Patterns).
PATTERN='(from fastapi import APIRouter|@router\.(post|get|put|delete|patch)|@app\.(post|get|put|delete|patch)|raise HTTPException)'

MATCHES="$(grep -rn --include='*.py' -E "$PATTERN" backend/app/ \
  | grep -v '^backend/app/main\.py:' \
  | { [ -s "$ALLOWLIST_TMP" ] && grep -v -F -f "$ALLOWLIST_TMP" || cat; } \
  | grep -v '# deviation:')"

if [ -n "$MATCHES" ]; then
  echo "FAIL: raw FastAPI patterns detected in backend/app/."
  echo "  See AGENTS.md § jvspatial Object-Spatial Contract (Forbidden Patterns)."
  echo ""
  echo "$MATCHES"
  echo ""
  echo "Options:"
  echo "  1. (preferred) Rewrite using jvspatial primitives (@endpoint, JVSpatialAPIException, schemas/)."
  echo "  2. If this is acknowledged debt under remediation, add the file to .ci/jvspatial_drift_allowlist.txt."
  echo "  3. If this is a measured intentional deviation, add an inline '# deviation: <reason> — <measurement>' comment."
  exit 1
fi

exit 0
