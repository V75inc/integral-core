#!/usr/bin/env bash
# Stop integral-core sandbox processes + optional Postgres container.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.sandbox/api.pid" ]]; then
  kill "$(cat "$ROOT/.sandbox/api.pid")" 2>/dev/null || true
  rm -f "$ROOT/.sandbox/api.pid"
  echo "stopped API"
fi
if [[ -f "$ROOT/.sandbox/fe.pid" ]]; then
  kill "$(cat "$ROOT/.sandbox/fe.pid")" 2>/dev/null || true
  rm -f "$ROOT/.sandbox/fe.pid"
  echo "stopped frontend"
fi

# Child vite/python sometimes orphan — free sandbox ports
for port in 4002 9007; do
  pid=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)
  if [[ -n "${pid:-}" ]]; then
    kill $pid 2>/dev/null || true
    echo "freed :$port"
  fi
done

if [[ "${1:-}" == "--db" ]]; then
  docker compose -f docker-compose.sandbox.yml down
  echo "stopped Postgres sandbox (volume retained)"
fi

echo "done (monorepo :4000/:9006/:5433 untouched)"
