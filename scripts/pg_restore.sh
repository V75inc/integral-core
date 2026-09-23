#!/usr/bin/env bash
# Restore an integral Postgres dump produced by scripts/pg_backup.sh.
#
# This exists because a backup nobody has restored is not a backup, it is an
# untested assumption. The drill is: run this against a scratch database and
# confirm the row counts, quarterly. See docs/ops/DEPLOY.md § Backups.
#
# Usage:
#   scripts/pg_restore.sh BACKUP_FILE                 # restore into $POSTGRES_DB
#   scripts/pg_restore.sh BACKUP_FILE --into scratch  # restore into a copy
#   scripts/pg_restore.sh BACKUP_FILE --drill         # restore into a temp DB,
#                                                     # report counts, drop it
#
# Restoring over a live database is destructive and irreversible, so the plain
# form requires typing the database name back when stdin is a TTY. `--drill`
# never touches the target database at all and is the safe way to rehearse.
#
# Exit codes: 0 ok, 1 restore failed, 2 usage/config error, 3 refused.

set -uo pipefail

PGDUMP_DOCKER_IMAGE="${PGDUMP_DOCKER_IMAGE:-pgvector/pgvector:pg16}"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit "${2:-1}"; }

# Docker first — see the matching note in pg_backup.sh. A host pg_restore older
# than the server refuses to run, and the pinned image matches the stack's
# Postgres by construction.
run_pg() {
  local bin="$1"; shift
  local dir; dir="$(cd "$(dirname "${BACKUP_FILE:-.}")" && pwd)"
  if command -v docker >/dev/null 2>&1; then
    docker run --rm -i --network host \
      -e PGPASSWORD="${POSTGRES_PASSWORD:-}" \
      -v "$dir:$dir" \
      "$PGDUMP_DOCKER_IMAGE" "$bin" "$@"
  elif command -v "$bin" >/dev/null 2>&1; then
    PGPASSWORD="${POSTGRES_PASSWORD:-}" "$bin" "$@"
  else
    die "neither docker nor $bin is available" 2
  fi
}

DRILL_DB=""
cleanup() {
  if [ -n "$DRILL_DB" ]; then
    log "drill: dropping scratch database $DRILL_DB"
    run_pg psql --host "$POSTGRES_HOST" --port "$POSTGRES_PORT" \
      --username "$POSTGRES_USER" --dbname postgres \
      -c "DROP DATABASE IF EXISTS \"$DRILL_DB\";" >/dev/null \
      || log "drill: WARNING — could not drop $DRILL_DB; remove it manually"
  fi
}
trap cleanup EXIT

BACKUP_FILE="${1:-}"
[ -n "$BACKUP_FILE" ] || die "usage: $(basename "$0") BACKUP_FILE [--into DB | --drill]" 2
[ -s "$BACKUP_FILE" ] || die "no such backup file: $BACKUP_FILE" 2
shift

MODE="in-place"
TARGET_DB=""
case "${1:-}" in
  --into)  TARGET_DB="${2:-}"; MODE="into"; [ -n "$TARGET_DB" ] || die "--into needs a database name" 2 ;;
  --drill) MODE="drill" ;;
  "")      ;;
  *)       die "unknown option: $1" 2 ;;
esac

: "${POSTGRES_USER:?POSTGRES_USER must be set}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set}"
POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5433}"

log "restore: validating $BACKUP_FILE"
run_pg pg_restore --list "$BACKUP_FILE" >/dev/null 2>&1 \
  || die "backup file is unreadable — do not trust it"

if [ "$MODE" = "drill" ]; then
  : "${POSTGRES_DB:?POSTGRES_DB must be set so the drill can compare counts}"
  TARGET_DB="integral_drill_$(date -u +%Y%m%d%H%M%S)"
  DRILL_DB="$TARGET_DB"
  log "drill: creating scratch database $TARGET_DB"
  run_pg psql --host "$POSTGRES_HOST" --port "$POSTGRES_PORT" \
    --username "$POSTGRES_USER" --dbname postgres \
    -c "CREATE DATABASE \"$TARGET_DB\";" >/dev/null || die "could not create scratch database"
elif [ "$MODE" = "into" ]; then
  log "restore: target database $TARGET_DB"
else
  : "${POSTGRES_DB:?POSTGRES_DB must be set for an in-place restore}"
  TARGET_DB="$POSTGRES_DB"
  # Destructive and irreversible. Require explicit confirmation when a human is
  # present; when run non-interactively, demand RESTORE_CONFIRM so a scheduled
  # job cannot silently overwrite production.
  if [ -t 0 ]; then
    printf 'This OVERWRITES database "%s" on %s. Type the database name to continue: ' \
      "$TARGET_DB" "$POSTGRES_HOST"
    read -r reply
    [ "$reply" = "$TARGET_DB" ] || die "refused: confirmation did not match" 3
  elif [ "${RESTORE_CONFIRM:-}" != "$TARGET_DB" ]; then
    die "refused: set RESTORE_CONFIRM=$TARGET_DB to restore in place non-interactively" 3
  fi
fi

log "restore: loading into $TARGET_DB"
# --clean --if-exists so a re-run is idempotent; --no-owner/--no-privileges to
# match how the dump was taken.
run_pg pg_restore \
  --host "$POSTGRES_HOST" --port "$POSTGRES_PORT" \
  --username "$POSTGRES_USER" --dbname "$TARGET_DB" \
  --clean --if-exists --no-owner --no-privileges \
  "$BACKUP_FILE"
STATUS=$?

# pg_restore exits non-zero on non-fatal notices too, so report counts and let
# the operator judge rather than declaring success or failure on exit code alone.
log "restore: pg_restore exit=$STATUS; row counts follow"
run_pg psql --host "$POSTGRES_HOST" --port "$POSTGRES_PORT" \
  --username "$POSTGRES_USER" --dbname "$TARGET_DB" -c "
    SELECT relname AS table, n_live_tup AS approx_rows
    FROM pg_stat_user_tables ORDER BY n_live_tup DESC LIMIT 10;" || true

if [ "$MODE" = "drill" ]; then
  graph_counts() {
    local db="$1"
    run_pg psql --host "$POSTGRES_HOST" --port "$POSTGRES_PORT" \
      --username "$POSTGRES_USER" --dbname "$db" -At -F '|' -c "
        SELECT 'edge', count(*) FROM edge
        UNION ALL SELECT 'node', count(*) FROM node
        UNION ALL SELECT 'object', count(*) FROM object
        ORDER BY 1;"
  }
  SOURCE_COUNTS="$(graph_counts "$POSTGRES_DB")" || die "drill: could not count source database"
  RESTORED_COUNTS="$(graph_counts "$TARGET_DB")" || die "drill: could not count restored database"
  if [ "$SOURCE_COUNTS" != "$RESTORED_COUNTS" ]; then
    log "drill: source counts: $SOURCE_COUNTS"
    log "drill: restored counts: $RESTORED_COUNTS"
    die "drill: restored node, edge, and object counts do not match the source"
  fi
  identity_sql="
    SELECT md5(coalesce(string_agg(line, E'\\n' ORDER BY line), ''))
    FROM (
      SELECT id || '|' || coalesce(data#>>'{context,version}','') || '|'
        || coalesce(data#>>'{context,metadata,slug}','') || '|'
        || coalesce(data#>>'{context,name}','') AS line
      FROM node WHERE entity = 'OperationalModel'
      UNION ALL
      SELECT id || '|' || coalesce(data#>>'{context,content_hash}','') || '|'
        || coalesce(data#>>'{context,size}','') || '|'
        || coalesce(data#>>'{context,storage_key}','')
      FROM node WHERE entity = 'Attachment'
    ) rows;"
  graph_identity() {
    local db="$1"
    run_pg psql --host "$POSTGRES_HOST" --port "$POSTGRES_PORT" \
      --username "$POSTGRES_USER" --dbname "$db" -At -c "$identity_sql"
  }
  SOURCE_IDENTITY="$(graph_identity "$POSTGRES_DB")" || die "drill: could not fingerprint source"
  RESTORED_IDENTITY="$(graph_identity "$TARGET_DB")" || die "drill: could not fingerprint restore"
  if [ "$SOURCE_IDENTITY" != "$RESTORED_IDENTITY" ]; then
    die "drill: package and attachment identity do not match the source"
  fi
  log "drill: counts match"
  log "drill: identity match"
  log "drill: complete — the dump restores the same node, edge, and object counts"
fi

exit 0
