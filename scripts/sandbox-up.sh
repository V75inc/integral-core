#!/usr/bin/env bash
# Start integral-core sandbox: isolated Postgres + API :4002 + SPA :9007.
# Leaves commercial monorepo (:5433 / :4000 / :9006) untouched.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="$ROOT/.env.sandbox"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE — copy from .env.sandbox.example and set JWT + CREDENTIAL keys."
  exit 1
fi

echo "==> Postgres sandbox (host :5435, db integral_core)"
docker compose -f docker-compose.sandbox.yml up -d
ready=0
for _ in $(seq 1 40); do
  if docker compose -f docker-compose.sandbox.yml exec -T db \
    pg_isready -U integral -d integral_core >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [[ "$ready" -ne 1 ]]; then
  echo "Postgres sandbox failed to become ready" >&2
  exit 1
fi

echo "==> Backend deps"
cd "$ROOT/backend"
if [[ ! -d .venv ]]; then
  uv sync --frozen --extra dev --extra test
fi

mkdir -p "$ROOT/.sandbox/logs"
API_LOG="$ROOT/.sandbox/logs/api.log"
FE_LOG="$ROOT/.sandbox/logs/fe.log"

# Stop prior sandbox PIDs if present
if [[ -f "$ROOT/.sandbox/api.pid" ]]; then
  kill "$(cat "$ROOT/.sandbox/api.pid")" 2>/dev/null || true
  rm -f "$ROOT/.sandbox/api.pid"
fi
if [[ -f "$ROOT/.sandbox/fe.pid" ]]; then
  kill "$(cat "$ROOT/.sandbox/fe.pid")" 2>/dev/null || true
  rm -f "$ROOT/.sandbox/fe.pid"
fi

echo "==> API on :4002 (INTEGRAL_CORE_ONLY=1)"
# Load sandbox env into this process; do not touch monorepo or root .env consumers.
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
# Force isolation even if .env.sandbox is incomplete
export JVSPATIAL_PORT=4002
export JVSPATIAL_POSTGRES_DSN="${JVSPATIAL_POSTGRES_DSN:-postgresql://integral:integral@localhost:5435/integral_core}"
export INTEGRAL_CORE_ONLY=1
# Reloader + WatchFiles is flaky under nohup; sandbox prefers a stable single process.
export DEBUG=false
export JVSPATIAL_DEBUG=false

# Detach from the calling TTY/session so Cursor agent shells (and SIGHUP)
# do not tear the sandbox down when the parent exits.
if command -v setsid >/dev/null 2>&1; then
  setsid "$ROOT/backend/.venv/bin/python" -m app.main </dev/null >"$API_LOG" 2>&1 &
else
  nohup "$ROOT/backend/.venv/bin/python" -m app.main </dev/null >"$API_LOG" 2>&1 &
fi
echo $! >"$ROOT/.sandbox/api.pid"
disown $! 2>/dev/null || true

echo "==> Frontend on :9007 → API :4002"
cd "$ROOT/frontend"
if [[ ! -d node_modules ]]; then
  npm install
fi
export VITE_BACKEND_URL=http://localhost:4002
# Call vite directly — `npm run dev -- --port` still expands package.json's --port 9006 first.
if command -v setsid >/dev/null 2>&1; then
  setsid npx vite --port 9007 --host 0.0.0.0 </dev/null >"$FE_LOG" 2>&1 &
else
  nohup npx vite --port 9007 --host 0.0.0.0 </dev/null >"$FE_LOG" 2>&1 &
fi
echo $! >"$ROOT/.sandbox/fe.pid"
disown $! 2>/dev/null || true

# Smoke wait
for _ in $(seq 1 40); do
  if curl -sf "http://127.0.0.1:4002/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

echo
echo "Sandbox up:"
echo "  API   http://localhost:4002/docs"
echo "  SPA   http://localhost:9007"
echo "  PG    localhost:5435 / integral_core  (container integral-core-sandbox-pg)"
echo "  logs  $ROOT/.sandbox/logs/"
echo
echo "Stop:  ./scripts/sandbox-down.sh"
