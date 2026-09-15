#!/usr/bin/env bash
# Resolve a Python interpreter for backend pre-commit hooks.
# Prefers a local backend venv (.venv or venv) when present; otherwise
# python3, then python. Covers Windows Scripts/ and Unix bin/ layouts.

resolve_python() {
  local repo_root="${1:-}"

  if [ -n "$repo_root" ]; then
    local candidate
    for candidate in \
      "$repo_root/backend/.venv/Scripts/python.exe" \
      "$repo_root/backend/venv/Scripts/python.exe" \
      "$repo_root/backend/.venv/bin/python" \
      "$repo_root/backend/venv/bin/python"
    do
      if [ -x "$candidate" ]; then
        printf '%s\n' "$candidate"
        return 0
      fi
    done
  fi

  if command -v python3 >/dev/null 2>&1; then
    printf '%s\n' python3
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    printf '%s\n' python
    return 0
  fi

  echo "backend hook: no python interpreter found (cd backend && uv sync --frozen --extra dev --extra test)" >&2
  return 1
}
