#!/usr/bin/env bash
# Post-roll migration: strip legacy node ``edges`` arrays from JSONB
# (jvspatial 0.0.18+). Adjacency always lives in the edge table; this only
# reclaims denormalised arrays left by older builds.
#
# Idempotent and safe online.
#
# Usage:
#   JVSPATIAL_POSTGRES_DSN=postgresql://… ./deploy/scripts/strip_node_edges.sh
#   # or
#   ./deploy/scripts/strip_node_edges.sh --dsn "$DSN"
#
# Requires the ``jvspatial`` CLI on PATH (same image / venv as the API).

set -euo pipefail

DSN="${JVSPATIAL_POSTGRES_DSN:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dsn)
      DSN="$2"
      shift 2
      ;;
    -h|--help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$DSN" ]]; then
  echo "FAIL: set JVSPATIAL_POSTGRES_DSN or pass --dsn" >&2
  exit 1
fi

if ! command -v jvspatial >/dev/null 2>&1; then
  echo "FAIL: jvspatial CLI not on PATH" >&2
  exit 1
fi

echo "Running: jvspatial migrate strip-node-edges --dsn <redacted> --apply"
jvspatial migrate strip-node-edges --dsn "$DSN" --apply
echo "OK: strip-node-edges complete"
