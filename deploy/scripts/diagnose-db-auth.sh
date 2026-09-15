#!/usr/bin/env bash
# Print why Swarm api cannot authenticate to in-stack Postgres.
# Run on the Swarm manager from repo root or deploy dir:
#   bash deploy/scripts/diagnose-db-auth.sh integral-dev

set -euo pipefail

STACK="${1:-integral-dev}"

echo "=== diagnose-db-auth: stack=${STACK} ==="

if [[ -f .env.deploy ]]; then
  echo "--- .env.deploy POSTGRES_* (deploy-time source) ---"
  grep -E '^POSTGRES_' .env.deploy || echo "(none)"
else
  echo "--- .env.deploy not found in $(pwd) ---"
fi

if [[ -f docker-stack.api-env.overlay.yml ]]; then
  echo "--- overlay POSTGRES_* / JVSPATIAL_POSTGRES_DSN (must be absent) ---"
  if grep -E 'POSTGRES_|JVSPATIAL_POSTGRES_DSN' docker-stack.api-env.overlay.yml; then
    echo "^^^ STALE OVERLAY — regenerate with deploy/ci-generate-api-env-overlay.sh"
  else
    echo "(none — OK)"
  fi
else
  echo "--- docker-stack.api-env.overlay.yml not in $(pwd) ---"
fi

API_ID="$(docker ps -q -f "name=${STACK}_api" | head -1)"
DB_ID="$(docker ps -q -f "name=${STACK}_db" | head -1)"

if [[ -z "$API_ID" ]]; then
  echo "ERROR: no running api container for ${STACK}_api" >&2
  docker service ps "${STACK}_api" --no-trunc 2>/dev/null || true
  exit 1
fi
if [[ -z "$DB_ID" ]]; then
  echo "ERROR: no running db container for ${STACK}_db" >&2
  docker service ps "${STACK}_db" --no-trunc 2>/dev/null || true
  exit 1
fi

echo "--- api container POSTGRES_* ---"
docker exec "$API_ID" sh -c 'printenv | grep -E "^POSTGRES_" | sort' || true
echo "--- db container POSTGRES_* ---"
docker exec "$DB_ID" sh -c 'printenv | grep -E "^POSTGRES_" | sort' || true

API_PW="$(docker exec "$API_ID" printenv POSTGRES_PASSWORD 2>/dev/null || true)"
DB_PW="$(docker exec "$DB_ID" printenv POSTGRES_PASSWORD 2>/dev/null || true)"
API_DB="$(docker exec "$API_ID" printenv POSTGRES_DB 2>/dev/null || true)"
DB_DB="$(docker exec "$DB_ID" printenv POSTGRES_DB 2>/dev/null || true)"

echo "--- summary ---"
echo "api POSTGRES_PASSWORD=${API_PW:-<unset>}"
echo "db  POSTGRES_PASSWORD=${DB_PW:-<unset>}"
echo "api POSTGRES_DB=${API_DB:-<unset>}"
echo "db  POSTGRES_DB=${DB_DB:-<unset>}"

if [[ -n "$API_PW" && -n "$DB_PW" && "$API_PW" != "$DB_PW" ]]; then
  echo "MISMATCH: api and db container env passwords differ."
fi

echo "--- tcp reachability api -> db:5432 ---"
docker exec "$API_ID" sh -c 'command -v nc >/dev/null && nc -z -w3 db 5432 && echo OK || echo "nc missing or db unreachable"' || true

echo "--- psql from db container (local socket — no password) ---"
docker exec "$DB_ID" psql -U integral -d postgres -tc "SELECT 1" >/dev/null && echo "local psql OK" || echo "local psql FAILED"

TARGET_PW="${DB_PW:-${API_PW:-integral}}"
echo "--- psql from db container with password from env (simulates api) ---"
if docker exec -e PGPASSWORD="$TARGET_PW" "$DB_ID" \
  psql -h localhost -U integral -d "${DB_DB:-integral_dev}" -tc "SELECT 1" 2>/dev/null | grep -q 1; then
  echo "TCP auth with db env password OK"
else
  echo "TCP auth with db env password FAILED — volume password differs from container env"
  echo "Run: bash deploy/scripts/fix-swarm-db-auth.sh ${STACK}"
fi
