#!/usr/bin/env bash
# Node.destroy guard — jvspatial has no ``destroy()``; every ``.destroy(``
# call raises AttributeError at runtime. Deletes go through
# ``node.delete(cascade=False)`` or a service-level cascade primitive
# (``entry_deletion.delete_entry_fast``, ``app_deletion.delete_app_cascade``,
# ``app_deletion.delete_track_and_nested_content``).
#
# Scans the WHOLE working tree under backend/app (not just the staged index):
# a call that only fails inside a compensation try/except is invisible to
# every other gate, so pre-existing drift is a real defect, not noise.

set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"
SCAN_DIR="$REPO_ROOT/backend/app"

if [ ! -d "$SCAN_DIR" ]; then
  exit 0
fi

# Plain grep on purpose: the hook runs under bash where ``rg`` may not be on
# PATH, and a missing scanner must not read as "clean".
VIOLATIONS=$(grep -rn --include='*.py' -E '\.destroy\(' "$SCAN_DIR" || true)
if [ -n "$VIOLATIONS" ]; then
  echo "node-destroy: '.destroy(' is not a jvspatial API (AttributeError at runtime):"
  echo "$VIOLATIONS"
  echo "Use node.delete(cascade=False) or the service cascade primitives"
  echo "(entry_deletion.delete_entry_fast / app_deletion.delete_app_cascade)."
  exit 1
fi

echo "node-destroy: clean."
