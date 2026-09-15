#!/usr/bin/env bash
# Resolve VITE_API_URL for the frontend Docker build (split-host dev deploy).
#
# Priority:
#   1. Non-empty VITE_API_URL in the shell (e.g. GitHub Actions variable override)
#   2. VITE_API_URL= in the env file (DEV_ENV_FILE / PROD_ENV_FILE body)
#   3. DOMAIN_API= in the env file → https://<host>
set -euo pipefail

env_file="${1:?env file path required}"

_read_env_key() {
  local key="$1"
  grep -E "^[[:space:]]*${key}=" "$env_file" 2>/dev/null | tail -1 | cut -d= -f2- |
    sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | tr -d '\r"' | tr -d "'"
}

_normalize_origin() {
  local value="$1"
  case "$value" in
    http://* | https://*) printf '%s' "${value%/}" ;;
    *) printf 'https://%s' "${value%/}" ;;
  esac
}

if [ -n "${VITE_API_URL:-}" ]; then
  _normalize_origin "${VITE_API_URL}"
  exit 0
fi

if [ ! -f "$env_file" ]; then
  echo "ci-resolve-vite-api-url: file not found: $env_file" >&2
  exit 1
fi

from_file="$(_read_env_key VITE_API_URL)"
if [ -n "$from_file" ]; then
  _normalize_origin "$from_file"
  exit 0
fi

domain_api="$(_read_env_key DOMAIN_API)"
if [ -n "$domain_api" ]; then
  _normalize_origin "$domain_api"
  exit 0
fi

domain="$(_read_env_key DOMAIN)"
if [ -n "$domain" ]; then
  _normalize_origin "$domain"
  exit 0
fi

echo "ci-resolve-vite-api-url: set VITE_API_URL, DOMAIN_API, or DOMAIN in $env_file" >&2
exit 1
