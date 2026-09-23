#!/usr/bin/env bash
# Copy EXAMPLE → DEST if DEST is missing, then replace placeholder secrets.
# Usage: bootstrap_env.sh DEST EXAMPLE
set -euo pipefail

DEST="${1:?usage: bootstrap_env.sh DEST EXAMPLE}"
EXAMPLE="${2:?usage: bootstrap_env.sh DEST EXAMPLE}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

resolve_python() {
  for cmd in python3 python py; do
    if command -v "$cmd" >/dev/null 2>&1; then
      printf '%s\n' "$cmd"
      return 0
    fi
  done
  echo "bootstrap_env: neither python3 nor python found in PATH" >&2
  return 1
}

PYTHON_BIN="$(resolve_python)"
exec "$PYTHON_BIN" "$SCRIPT_DIR/bootstrap_env.py" "$DEST" "$EXAMPLE"
