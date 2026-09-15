#!/usr/bin/env bash
# Run the Integral backend (FastAPI :4000) and the jvagent server (cockpit :8787)
# in parallel. Tags log lines by source so they're easy to read interleaved.
#
# Stop with Ctrl-C — both child processes are killed.
#
# Configuration (env vars, all optional):
#   BACKEND_PYTHON   — Python interpreter for the Integral backend
#                      (default: backend/venv/bin/python)
#   JVAGENT_BIN      — Path to jvagent CLI
#                      (default: ~/Briefcase/dev/jv/jvagent/.venv/bin/jvagent)
#   SKIP_BACKEND=1   — Run only the jvagent server
#   SKIP_JVAGENT=1   — Run only the Integral backend
#
# This script does not source any .env files itself — each subprocess loads
# its own (backend reads ./.env, jvagent reads agent/.env).

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

BACKEND_PYTHON="${BACKEND_PYTHON:-$REPO_ROOT/backend/venv/bin/python}"
JVAGENT_BIN="${JVAGENT_BIN:-$HOME/Briefcase/dev/jv/jvagent/.venv/bin/jvagent}"

# ANSI color prefixes (skip if not a TTY).
if [[ -t 1 ]]; then
  C_BACKEND=$'\033[36m'   # cyan
  C_JVAGENT=$'\033[35m'   # magenta
  C_RESET=$'\033[0m'
else
  C_BACKEND=''; C_JVAGENT=''; C_RESET=''
fi

prefix() {
  local tag="$1" color="$2"
  while IFS= read -r line; do
    printf '%s[%s]%s %s\n' "$color" "$tag" "$C_RESET" "$line"
  done
}

PIDS=()

cleanup() {
  echo
  echo "==> stopping…"
  for pid in "${PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

if [[ "${SKIP_BACKEND:-0}" != "1" ]]; then
  if [[ ! -x "$BACKEND_PYTHON" ]]; then
    echo "error: BACKEND_PYTHON not executable: $BACKEND_PYTHON" >&2
    echo "       hint: cd backend && uv sync --frozen --extra dev --extra test" >&2
    exit 1
  fi
  ( cd "$REPO_ROOT/backend" && "$BACKEND_PYTHON" -m app.main 2>&1 ) | prefix backend "$C_BACKEND" &
  PIDS+=($!)
fi

if [[ "${SKIP_JVAGENT:-0}" != "1" ]]; then
  if [[ ! -x "$JVAGENT_BIN" ]]; then
    echo "error: JVAGENT_BIN not executable: $JVAGENT_BIN" >&2
    echo "       hint: install jvagent (see agent/README.md)" >&2
    exit 1
  fi
  ( cd "$REPO_ROOT/agent" && "$JVAGENT_BIN" . 2>&1 ) | prefix jvagent "$C_JVAGENT" &
  PIDS+=($!)
fi

if [[ ${#PIDS[@]} -eq 0 ]]; then
  echo "error: nothing to run (both SKIP_BACKEND and SKIP_JVAGENT set)" >&2
  exit 1
fi

wait
