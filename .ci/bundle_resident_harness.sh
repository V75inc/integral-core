#!/usr/bin/env bash
# Copy the resident jvagent tree into the package so a wheel can boot it.
# The checkout copy at agent/ stays the source of truth.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
src="$root/agent"
dest="$root/backend/app/resident_harness"
rm -rf "$dest"
mkdir -p "$dest"
tar -C "$src" \
  --exclude '__pycache__' \
  --exclude '.env' \
  --exclude '*.pyc' \
  -cf - . | tar -C "$dest" -xf -
test -f "$dest/app.yaml"
test -f "$dest/agents/integral/integral_agent/agent.yaml"
