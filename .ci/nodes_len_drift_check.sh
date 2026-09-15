#!/usr/bin/env bash
# Drift guard: forbid ``len(await <node>.nodes(...))``.
#
# That pattern hydrates every neighbour then counts in Python. On jvspatial
# 0.0.18+, use ``await <node>.count_nodes(...)`` (one COUNT round trip) or
# ``nodes_page`` / ``nodes(..., limit=)`` for bounded lists.
#
# Pattern mirrors the Phase A gate in
# docs/superpowers/specs/2026-09-11-substrate-scale-remediation.md.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${REPO_ROOT}/backend/app"

if [ ! -d "$TARGET" ]; then
  echo "FAIL: expected backend/app at $TARGET"
  exit 1
fi

# Prefer staged index when available (pre-commit); fall back to working tree.
if git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  MATCHES=$(
    git -C "$REPO_ROOT" grep -nE 'len\(\s*await\s+[A-Za-z0-9_.]+\.nodes\(' \
      --cached -- 'backend/app/**/*.py' 2>/dev/null \
    || git -C "$REPO_ROOT" grep -nE 'len\(\s*await\s+[A-Za-z0-9_.]+\.nodes\(' \
      -- 'backend/app/**/*.py' 2>/dev/null \
    || true
  )
else
  MATCHES=$(
    rg -n 'len\(\s*await\s+[A-Za-z0-9_.]+\.nodes\(' "$TARGET" --glob '*.py' || true
  )
fi

if [ -n "$MATCHES" ]; then
  echo "FAIL: found len(await ….nodes(…)) — use count_nodes() / nodes_page / limit= instead:"
  echo "$MATCHES"
  echo ""
  echo "See docs/superpowers/specs/2026-09-11-substrate-scale-remediation.md Phase A.4"
  exit 1
fi

echo "OK: no len(await ….nodes(…)) drift in backend/app"
exit 0
