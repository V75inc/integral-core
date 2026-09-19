#!/usr/bin/env bash
# Poll URL until it returns HTTP success, or fail.
# Usage: wait_for_http.sh URL [attempts] [sleep_seconds] [pidfile]
set -euo pipefail

URL="${1:?usage: wait_for_http.sh URL [attempts] [sleep] [pidfile]}"
ATTEMPTS="${2:-40}"
SLEEP="${3:-0.5}"
PIDFILE="${4:-}"

for _ in $(seq 1 "$ATTEMPTS"); do
  if [[ -n "$PIDFILE" && -f "$PIDFILE" ]]; then
    if ! kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "wait_for_http: process in $PIDFILE is not running" >&2
      exit 2
    fi
  fi
  if curl -sf "$URL" >/dev/null 2>&1; then
    exit 0
  fi
  sleep "$SLEEP"
done

echo "wait_for_http: $URL not reachable after ${ATTEMPTS} attempts" >&2
exit 1
