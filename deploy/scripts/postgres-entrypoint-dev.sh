#!/usr/bin/env bash
# Dev Swarm only — sync Postgres role password with POSTGRES_PASSWORD on every start.
#
# The official postgres image applies POSTGRES_PASSWORD only when the data
# directory is first created. After that, changing .env.deploy does not update
# the stored password and the API gets "password authentication failed".
#
# Local socket auth (peer/trust) still works inside the container, so we ALTER
# USER after postgres is ready, then keep postgres in the foreground.
set -euo pipefail

if [ "${1:-}" != "postgres" ]; then
  exec docker-entrypoint.sh "$@"
fi

docker-entrypoint.sh "$@" &
pg_pid=$!

user="${POSTGRES_USER:-integral}"
password="${POSTGRES_PASSWORD:-integral}"
dbname="${POSTGRES_DB:-integral_dev}"

for _ in $(seq 1 120); do
  if pg_isready -U "$user" -d "$dbname" -q 2>/dev/null; then
    break
  fi
  sleep 1
done

escaped_pw="${password//\'/\'\'}"
for attempt in 1 2 3 4 5; do
  if psql -v ON_ERROR_STOP=1 --username "$user" --dbname "postgres" <<-EOSQL
ALTER USER "${user}" WITH PASSWORD '${escaped_pw}';
EOSQL
  then
    break
  fi
  sleep 2
done

if ! psql -v ON_ERROR_STOP=1 --username "$user" --dbname "postgres" -tc \
  "SELECT 1 FROM pg_database WHERE datname = '${dbname}'" | grep -q 1; then
  psql -v ON_ERROR_STOP=1 --username "$user" --dbname "postgres" \
    -c "CREATE DATABASE \"${dbname}\";"
fi

wait "$pg_pid"
