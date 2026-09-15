#!/usr/bin/env bash
# One-shot fix for "password authentication failed for user integral" on Swarm dev.
#
# Postgres stores the password from first volume init; later .env.deploy changes do
# not update it. This script aligns the role password with the db container env and
# restarts api.
#
#   bash deploy/scripts/fix-swarm-db-auth.sh integral-dev

set -euo pipefail

STACK="${1:-integral-dev}"

DB_ID="$(docker ps -q -f "name=${STACK}_db" | head -1)"
if [[ -z "$DB_ID" ]]; then
  echo "fix-swarm-db-auth: no running db container for ${STACK}_db" >&2
  exit 1
fi

USER="$(docker exec "$DB_ID" printenv POSTGRES_USER 2>/dev/null || echo integral)"
DB="$(docker exec "$DB_ID" printenv POSTGRES_DB 2>/dev/null || echo integral_dev)"
PW="$(docker exec "$DB_ID" printenv POSTGRES_PASSWORD 2>/dev/null || echo integral)"
if [[ -z "$PW" ]]; then
  PW="integral"
fi

echo "fix-swarm-db-auth: ALTER USER ${USER} on ${DB_ID} (db=${DB})"
escaped="${PW//\'/\'\'}"
docker exec "$DB_ID" psql -U "$USER" -d postgres \
  -c "ALTER USER \"${USER}\" WITH PASSWORD '${escaped}';"

echo "fix-swarm-db-auth: restarting db then api"
docker service update --force "${STACK}_db" >/dev/null
sleep 8
docker service update --force "${STACK}_api" >/dev/null

echo "fix-swarm-db-auth: done — logs: docker service logs ${STACK}_api --tail 40"
