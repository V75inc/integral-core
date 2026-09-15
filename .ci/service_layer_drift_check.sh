#!/usr/bin/env bash
# I-CRUD-01 service-layer drift guard.
#
# Fails if backend/app/api/ or backend/app/agentive/ (excluding services/)
# call Node.create( or .connect( on graph entities outside allowlist.
#
# Also ratchets allowlist size: entries cannot grow beyond MAX_ALLOWLIST_ENTRIES.
# To shrink the list, remove paths and lower MAX after verifying CI passes.
#
# See docs/backend/service-layer.md for canonical entrypoints.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ALLOWLIST="$REPO_ROOT/.ci/service_layer_drift_allowlist.txt"
# Frozen baseline — update only when removing entries (never increase).
MAX_ALLOWLIST_ENTRIES=17

cd "$REPO_ROOT"

ALLOWLIST_TMP="$(mktemp)"
trap 'rm -f "$ALLOWLIST_TMP"' EXIT
grep -v '^\s*#' "$ALLOWLIST" | grep -v '^\s*$' > "$ALLOWLIST_TMP" || true

ALLOWLIST_COUNT="$(wc -l < "$ALLOWLIST_TMP" | tr -d ' ')"
if [ "$ALLOWLIST_COUNT" -gt "$MAX_ALLOWLIST_ENTRIES" ]; then
  echo "FAIL: service_layer_drift_allowlist grew ($ALLOWLIST_COUNT > $MAX_ALLOWLIST_ENTRIES)."
  echo "  Drain debt by moving Node.create/.connect into service functions, then"
  echo "  remove allowlist paths — do NOT expand MAX_ALLOWLIST_ENTRIES without"
  echo "  a substrate-touching plan."
  exit 1
fi

PATTERN='\.(create\(|connect\()'

# No trailing slashes on search roots. BSD grep (macOS) prints
# ``dir//file`` when the root ends in ``/``, which then fails to match
# allowlist paths like ``backend/app/api/auth.py``.
MATCHES="$(grep -rn --include='*.py' -E "$PATTERN" backend/app/api backend/app/agentive \
  | grep -v '/services/' \
  | grep -v '__pycache__' \
  | { [ -s "$ALLOWLIST_TMP" ] && grep -v -F -f "$ALLOWLIST_TMP" || cat; } \
  | grep -v '# deviation:')"

if [ -n "$MATCHES" ]; then
  echo "FAIL: direct Node.create/.connect in api/ or agentive/ outside allowlist."
  echo "  See docs/backend/service-layer.md and I-CRUD-01 in docs/INVARIANTS.md."
  echo ""
  echo "$MATCHES"
  echo ""
  echo "Delegate to canonical service functions or add to .ci/service_layer_drift_allowlist.txt with reason."
  exit 1
fi

exit 0
