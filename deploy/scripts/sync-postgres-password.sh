#!/usr/bin/env bash
# Align Postgres role password with .env.deploy / stack POSTGRES_PASSWORD.
#
# Postgres only reads POSTGRES_PASSWORD on first volume init. After a password
# change in DEV_ENV_FILE, run this on the Swarm manager before redeploying api:
#
#   set -a && source .env.deploy && set +a
#   bash deploy/scripts/sync-postgres-password.sh integral-dev
#
# Dev-only nuclear reset (destroys DB data):
#   docker stack rm integral-dev
#   docker volume rm integral-dev_postgres_data
#   # redeploy stack

set -euo pipefail

STACK="${1:-integral-dev}"
PW="${POSTGRES_PASSWORD:-integral}"
USER="${POSTGRES_USER:-integral}"
DB="${POSTGRES_DB:-integral_dev}"

DB_ID="$(docker ps -q -f "name=${STACK}_db" | head -1)"
if [[ -z "$DB_ID" ]]; then
  echo "sync-postgres-password: no running db container for stack ${STACK}" >&2
  exit 1
fi

echo "sync-postgres-password: aligning role ${USER} on ${DB_ID} (db=${DB})"
docker exec "$DB_ID" psql -U "$USER" -d "$DB" \
  -c "ALTER USER \"${USER}\" PASSWORD '${PW//\'/\'\'}';"

echo "sync-postgres-password: forcing api service restart"
docker service update --force "${STACK}_api" >/dev/null

echo "sync-postgres-password: done — check logs: docker service logs ${STACK}_api --tail 30"
