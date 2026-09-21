#!/usr/bin/env bash
# F0/OSS extract guard — backend/app/packages/ must contain only core seeds.
# Domain Apps live under packages/apps/ (commercial), never in the Core tree.

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILES="$REPO_ROOT/backend/app/packages"
ALLOWED='^(agent-scratch|__pycache__|__init__\.py)$'

if [ ! -d "$PROFILES" ]; then
  echo "core-profiles-only: FAIL — missing $PROFILES" >&2
  exit 1
fi

bad=0
while IFS= read -r -d '' entry; do
  name=$(basename "$entry")
  if ! echo "$name" | grep -Eq "$ALLOWED"; then
    echo "core-profiles-only: unexpected under app/packages/: $name" >&2
    bad=1
  fi
done < <(find "$PROFILES" -mindepth 1 -maxdepth 1 -print0)

# Also fail if a operational-model.yaml under app/packages declares non-core class.
while IFS= read -r -d '' yaml; do
  if grep -Eq 'class:[[:space:]]*(community_app|verified_app|commercial_app|private_org_app)' "$yaml"; then
    echo "core-profiles-only: non-core package.class in $yaml" >&2
    bad=1
  fi
done < <(find "$PROFILES" -name operational-model.yaml -print0 2>/dev/null || true)

if [ "$bad" -ne 0 ]; then
  echo "core-profiles-only: FAIL — move domain packages to packages/apps/" >&2
  exit 1
fi

echo "core-profiles-only: OK (seeds only under backend/app/packages/)"
