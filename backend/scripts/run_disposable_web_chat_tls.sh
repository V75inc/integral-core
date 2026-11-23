#!/usr/bin/env bash
# Opt-in checkout fixture only. Never supply an existing Integral database.
set -euo pipefail
umask 077

root="$(cd "$(dirname "$0")/../.." && pwd)"
py="${PREVIEW_PYTHON:-$root/backend/.venv/bin/python}"
host="${PREVIEW_HOST:-conversation.example.invalid}"
port="${PREVIEW_PORT:-58173}"
pg_port="${PREVIEW_PG_PORT:-55523}"
ttl="${PREVIEW_SECONDS:-900}"
case "$host" in
  ''|*[!a-z0-9.-]*|localhost|*.localhost|127.*|0.0.0.0)
    echo "Use an approved canonical non-localhost DNS name for this fixture" >&2
    exit 2
    ;;
esac
for value in "$port" "$pg_port"; do
  [[ "$value" =~ ^[1-9][0-9]{3,4}$ ]] && (( value >= 1024 && value <= 65535 )) || exit 2
  [[ "$value" != 9047 && "$value" != 4047 ]] || exit 2
done
[[ "$port" != "$pg_port" ]] || exit 2
[[ "$ttl" =~ ^[1-9][0-9]{0,3}$ ]] && (( ttl <= 3600 )) || exit 2
[[ -x "$py" ]] || { echo "Set PREVIEW_PYTHON to the reviewed Core test interpreter" >&2; exit 2; }
tmp="$(mktemp -d "${TMPDIR:-/tmp}/integral-web-chat-tls.XXXXXX")"
cleanup() {
  pg_ctl -D "$tmp/db" -m fast -w stop >/dev/null 2>&1 || true
  rm -rf -- "$tmp"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
mkdir "$tmp/home" "$tmp/socket"
if [[ -n "${PREVIEW_CERT:-}" || -n "${PREVIEW_KEY:-}" ]]; then
  [[ "${PREVIEW_CERT:-}" = /* && "${PREVIEW_KEY:-}" = /* ]] || exit 2
  cert="$PREVIEW_CERT"
  key="$PREVIEW_KEY"
else
  cert="$tmp/cert.pem"
  key="$tmp/key.pem"
  openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 1 \
    -keyout "$key" -out "$cert" -subj "/CN=$host" \
    -addext "subjectAltName=DNS:$host" >/dev/null 2>&1
  printf 'Fixture certificate for explicit curl --cacert only: %s\n' "$cert"
fi
[[ -r "$cert" && -r "$key" ]] || exit 2
(cd "$root/frontend" && npm run build:web-chat && npm run build:web-chat-embed)
initdb -U integral_preview -D "$tmp/db" -A trust >/dev/null
pg_ctl -D "$tmp/db" -o "-h 127.0.0.1 -p $pg_port -k $tmp/socket" -l "$tmp/pg.log" -w start
cd "$root/backend"
env -i HOME="$tmp/home" PATH="$PATH" TESTING=1 DEBUG=false \
  INTEGRAL_TEST_DB=postgres \
  INTEGRAL_TEST_POSTGRES_DSN="postgresql://integral_preview@127.0.0.1:$pg_port/postgres" \
  INTEGRAL_TEST_WEB_CHAT_DIST="$root/frontend/dist-web-chat" \
  INTEGRAL_TEST_WEB_CHAT_EMBED_DIST="$root/frontend/dist-web-chat-embed" \
  INTEGRAL_FIXTURE_WEB_CHAT_ORIGIN="https://$host:$port" \
  INTEGRAL_FIXTURE_WEB_CHAT_CERT="$cert" INTEGRAL_FIXTURE_WEB_CHAT_KEY="$key" \
  INTEGRAL_FIXTURE_WEB_CHAT_SECONDS="$ttl" \
  "$py" -m pytest -o addopts='' -q -s tests/contract/test_managed_web_chat_delivery_postgres.py \
    -k test_staff_and_public_web_chat_are_distinct_reviewed_hosts
