#!/usr/bin/env bash
# Curl-loop benchmark for Integral list endpoints. Captures p50/p95/p99.
#
# Usage:
#   BASE_URL=http://localhost:4000 \
#   JWT=<bearer> \
#   SCOPE=<workspace_id> \
#   ITERATIONS=30 \
#   OUT=.planning/perf-baseline-pre.txt \
#   backend/scripts/perf_bench.sh

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:4000}"
JWT="${JWT:-}"
SCOPE="${SCOPE:-}"
ITERATIONS="${ITERATIONS:-30}"
OUT="${OUT:-.planning/perf-baseline.txt}"
TRACK_ID="${TRACK_ID:-}"

if [[ -z "$JWT" ]]; then
  echo "ERROR: set JWT=<bearer token>" >&2
  exit 1
fi
if [[ -z "$SCOPE" ]]; then
  echo "ERROR: set SCOPE=<workspace_id>" >&2
  exit 1
fi

mkdir -p "$(dirname "$OUT")"

ENDPOINTS=(
  "/api/tracks"
  "/api/feed"
  "/api/me/shared"
  "/api/notifications?limit=20"
  "/api/me/mission-control"
)
if [[ -n "$TRACK_ID" ]]; then
  ENDPOINTS+=("/api/tracks/$TRACK_ID/entries?limit=20")
fi

percentile() {
  # $1 = percentile (0-100), $2 = sorted file
  local p="$1" f="$2" n
  n=$(wc -l < "$f")
  if [[ "$n" -eq 0 ]]; then echo "NA"; return; fi
  local idx
  idx=$(awk -v p="$p" -v n="$n" 'BEGIN{i=int((p/100)*n); if(i<1)i=1; if(i>n)i=n; print i}')
  sed -n "${idx}p" "$f"
}

{
  echo "# Integral perf bench"
  echo "# base_url=$BASE_URL  iterations=$ITERATIONS  scope=$SCOPE"
  echo "# captured: $(date -u +%FT%TZ)"
  echo
  printf "%-40s  %10s  %10s  %10s  %10s\n" "endpoint" "p50_ms" "p95_ms" "p99_ms" "max_ms"
  echo "---------------------------------------------------------------------------------------"

  for ep in "${ENDPOINTS[@]}"; do
    tmp=$(mktemp)
    # warm
    curl -s -o /dev/null \
      -H "Authorization: Bearer $JWT" \
      -H "X-Integral-Scope: $SCOPE" \
      "$BASE_URL$ep" || true

    for ((i=0; i<ITERATIONS; i++)); do
      curl -s -o /dev/null -w "%{time_total}\n" \
        -H "Authorization: Bearer $JWT" \
        -H "X-Integral-Scope: $SCOPE" \
        "$BASE_URL$ep" \
        | awk '{printf "%d\n", $1*1000}'
    done | sort -n > "$tmp"

    p50=$(percentile 50 "$tmp")
    p95=$(percentile 95 "$tmp")
    p99=$(percentile 99 "$tmp")
    pmax=$(tail -1 "$tmp")
    printf "%-40s  %10s  %10s  %10s  %10s\n" "$ep" "$p50" "$p95" "$p99" "$pmax"
    rm "$tmp"
  done
} | tee "$OUT"

echo
echo "Wrote $OUT"
