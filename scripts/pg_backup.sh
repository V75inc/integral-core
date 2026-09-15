#!/usr/bin/env bash
# Postgres logical backup for the Swarm stack's in-stack pgvector database.
#
# Context: deploy/docker-stack.prod.yml runs a single-replica Postgres on a
# local `postgres_data` volume. Before this script there was no backup of any
# kind -- docs/ops/DEPLOY.md described MongoDB Atlas snapshots, which do not
# apply to it. Losing that volume meant losing the graph.
#
# Scope, stated honestly: this is `pg_dump`, i.e. point-in-time-of-run logical
# backups. It bounds the loss to whatever has changed since the last run; it is
# NOT continuous archiving. If the acceptable data loss window is smaller than
# the schedule interval, this is the wrong tool and you want WAL archiving
# (wal-g / pgBackRest) or a managed Postgres with PITR. That is a deliberate
# infrastructure decision, not something to default into silently.
#
# Usage:
#   scripts/pg_backup.sh                      # dump using env / .env.deploy
#   BACKUP_DIR=/mnt/backups scripts/pg_backup.sh
#   scripts/pg_backup.sh --verify-only FILE   # re-check an existing dump
#
# Environment:
#   POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB   required
#   POSTGRES_HOST (default: localhost)  POSTGRES_PORT (default: 5433)
#   BACKUP_DIR    (default: ./backups)
#   BACKUP_RETAIN (default: 14)  -- dumps to keep; older ones are pruned
#   PGDUMP_DOCKER_IMAGE (default: pgvector/pgvector:pg16)
#
# Exit codes: 0 ok, 1 backup/verify failed, 2 usage/config error.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$REPO_ROOT/backups}"
BACKUP_RETAIN="${BACKUP_RETAIN:-14}"
PGDUMP_DOCKER_IMAGE="${PGDUMP_DOCKER_IMAGE:-pgvector/pgvector:pg16}"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit "${2:-1}"; }

# ---------------------------------------------------------------------------
# Verify: a dump that cannot be read is not a backup.
# ---------------------------------------------------------------------------
# Custom-format dumps are verified with `pg_restore --list`, which parses the
# archive's table of contents. A truncated or corrupt file fails here, which is
# the whole point -- an unverified dump is a promise, not a backup.
verify_dump() {
  local file="$1"
  [ -s "$file" ] || { log "verify: $file is empty or missing"; return 1; }
  if ! run_pg pg_restore --list "$file" >/dev/null 2>&1; then
    log "verify: pg_restore could not read $file"
    return 1
  fi
  local tables
  tables="$(run_pg pg_restore --list "$file" 2>/dev/null | grep -c 'TABLE DATA' || true)"
  log "verify: OK ($(du -h "$file" | cut -f1), ${tables:-0} table-data entries)"
  # A structurally valid but empty dump is usually a misconfigured DSN, not an
  # empty database. Flag it rather than reporting success.
  if [ "${tables:-0}" -eq 0 ]; then
    log "verify: WARNING — no table data in dump; check POSTGRES_DB / host"
  fi
  return 0
}

# Run a postgres client binary.
#
# Docker FIRST, deliberately. pg_dump refuses to dump a server newer than
# itself ("aborting because of server version mismatch"), and a host's package
# manager routinely lags the server: this was caught on a box with Homebrew
# pg_dump 14.17 against the stack's Postgres 16.14. Using the same image the
# stack already runs makes the client version match the server by construction.
# A local binary is only the fallback, for hosts without docker.
run_pg() {
  local bin="$1"; shift
  if command -v docker >/dev/null 2>&1; then
    docker run --rm -i --network host \
      -e PGPASSWORD="${POSTGRES_PASSWORD:-}" \
      -v "$BACKUP_DIR:$BACKUP_DIR" \
      "$PGDUMP_DOCKER_IMAGE" "$bin" "$@"
  elif command -v "$bin" >/dev/null 2>&1; then
    PGPASSWORD="${POSTGRES_PASSWORD:-}" "$bin" "$@"
  else
    die "neither docker nor $bin is available" 2
  fi
}

if [ "${1:-}" = "--verify-only" ]; then
  [ -n "${2:-}" ] || die "--verify-only needs a file path" 2
  verify_dump "$2" && exit 0 || exit 1
fi

: "${POSTGRES_USER:?POSTGRES_USER must be set}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set}"
: "${POSTGRES_DB:?POSTGRES_DB must be set}"
POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5433}"

mkdir -p "$BACKUP_DIR" || die "cannot create $BACKUP_DIR" 2

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
# .part until verified, so a partial file is never mistaken for a good backup
# by a restore script or a retention sweep.
PARTIAL="$BACKUP_DIR/integral-$STAMP.dump.part"
FINAL="$BACKUP_DIR/integral-$STAMP.dump"

log "backup: dumping $POSTGRES_DB from $POSTGRES_HOST:$POSTGRES_PORT"
if ! run_pg pg_dump \
    --host "$POSTGRES_HOST" --port "$POSTGRES_PORT" \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --format=custom --compress=9 --no-owner --no-privileges \
    --file "$PARTIAL"; then
  rm -f "$PARTIAL"
  die "pg_dump failed"
fi

if ! verify_dump "$PARTIAL"; then
  rm -f "$PARTIAL"
  die "dump failed verification and was discarded"
fi

mv "$PARTIAL" "$FINAL"
log "backup: wrote $FINAL"

# Retention. Only ever prunes verified dumps matching our own naming pattern,
# and never the newest one even if RETAIN is misconfigured to 0.
# NOTE: no `mapfile` here. It is a bash 4+ builtin and macOS still ships bash
# 3.2, where retention silently did nothing ("mapfile: command not found")
# while the script still reported success — backups would have accumulated
# unbounded on any dev/ops machine running the stock shell.
if [ "$BACKUP_RETAIN" -gt 0 ] 2>/dev/null; then
  ls -1t "$BACKUP_DIR"/integral-*.dump 2>/dev/null \
    | tail -n +"$((BACKUP_RETAIN + 1))" \
    | while IFS= read -r f; do
        [ -n "$f" ] || continue
        log "retention: pruning $(basename "$f")"
        rm -f "$f"
      done
fi

log "backup: done"
exit 0
