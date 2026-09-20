#!/usr/bin/env bash
# F0 — Core must not import App packages or domain plugins at runtime.
# Scans backend/app/{services,api,models,schemas} for forbidden imports.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

FAIL=0
# Profiles and plugins are App-layer; Core must not import them.
PATTERNS=(
  'from app\.profiles'
  'import app\.profiles'
  'from app\.plugins'
  'import app\.plugins'
)

SCOPE=(
  backend/app/services
  backend/app/api
  backend/app/models
  backend/app/schemas
)

_search_py() {
  local pat="$1" dir="$2"
  if command -v rg >/dev/null 2>&1; then
    rg -n --glob '*.py' -- "$pat" "$dir" || true
  elif command -v grep >/dev/null 2>&1; then
    grep -REn --include='*.py' -- "$pat" "$dir" || true
  else
    echo "core_no_app_import_check: need ripgrep (rg) or grep on PATH" >&2
    exit 1
  fi
}

# Allowed carve-outs: hook handler resolution builds import strings dynamically;
# do not flag comments that merely mention the path.
for dir in "${SCOPE[@]}"; do
  for pat in "${PATTERNS[@]}"; do
    matches=$(_search_py "$pat" "$dir")
    if [[ -n "$matches" ]]; then
      echo "FORBIDDEN import pattern /$pat/ under $dir:"
      echo "$matches"
      FAIL=1
    fi
  done
done

if [[ "$FAIL" -ne 0 ]]; then
  echo "core_no_app_import_check FAILED"
  exit 1
fi
echo "core_no_app_import_check OK"
exit 0
