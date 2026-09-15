#!/usr/bin/env bash
# Manual dev Swarm deploy from repo root on the manager node (mirrors CI).
#
#   cd ~/integral
#   cp deploy/.env.dev.example .env.deploy   # first time — fill in secrets
#   bash deploy/scripts/deploy-dev-swarm.sh
#
# Requires: docker swarm init, external network ``edge``, API_IMAGE + WEB_IMAGE in .env.deploy

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

STACK="${STACK_DEV:-integral-dev}"
ENV_FILE="${ENV_FILE:-.env.deploy}"
STACK_FILE="${STACK_FILE:-deploy/docker-stack.dev.yml}"
OVERLAY="${OVERLAY:-docker-stack.api-env.overlay.yml}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "deploy-dev-swarm: missing $ENV_FILE — copy deploy/.env.dev.example" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${API_IMAGE:?set API_IMAGE in $ENV_FILE}"
: "${WEB_IMAGE:?set WEB_IMAGE in $ENV_FILE}"

if [[ -z "${POSTGRES_IMAGE:-}" ]]; then
  echo "deploy-dev-swarm: building local postgres-dev image (password sync entrypoint)"
  POSTGRES_IMAGE="integral-dev-postgres:local"
  docker build -f deploy/Dockerfile.postgres-dev -t "$POSTGRES_IMAGE" .
fi
export POSTGRES_IMAGE

bash deploy/ci-generate-api-env-overlay.sh "$ENV_FILE" "$OVERLAY"

docker info | grep -q 'Swarm: active' || docker swarm init
docker network create --driver=overlay --attachable edge >/dev/null 2>&1 || true

docker pull "$API_IMAGE"
docker pull "$WEB_IMAGE"
if [[ "$POSTGRES_IMAGE" != integral-dev-postgres:local ]]; then
  docker pull "$POSTGRES_IMAGE"
fi

docker stack deploy --with-registry-auth \
  --compose-file "$STACK_FILE" \
  --compose-file "$OVERLAY" \
  --resolve-image always \
  "$STACK"

echo "deploy-dev-swarm: waiting for db..."
for _ in $(seq 1 60); do
  DB_ID="$(docker ps -q -f "name=${STACK}_db" | head -1 || true)"
  if [[ -n "$DB_ID" ]] && docker exec "$DB_ID" pg_isready -U "${POSTGRES_USER:-integral}" -q 2>/dev/null; then
    break
  fi
  sleep 2
done

bash deploy/scripts/fix-swarm-db-auth.sh "$STACK"
docker stack services "$STACK"
