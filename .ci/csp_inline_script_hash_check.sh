#!/usr/bin/env bash
# CSP inline-script hash guard.
#
# frontend/index.html carries two inline <script> blocks that must run before
# first paint (the `globalThis.process` shim and the theme bootstrap). Rather
# than weaken the SPA's Content-Security-Policy with `script-src 'unsafe-inline'`,
# frontend/nginx.conf and frontend/nginx.docker.conf allowlist them by sha256
# hash.
#
# That trade has one failure mode, and it is a nasty one: edit either inline
# script — even by a single character of whitespace — and the hash no longer
# matches. The browser then silently refuses to execute it. The theme bootstrap
# failing is a flash of the wrong theme; the `globalThis.process` shim failing
# is a white screen. Neither shows up in a build, a type check, or a unit test.
#
# This guard recomputes the hashes from frontend/index.html and fails if the
# nginx configs don't contain them.
#
# Unlike the drift guards, this scans the working tree rather than the index —
# the file pair either agrees or it doesn't, and there is no such thing as
# "pre-existing drift this commit didn't touch" for a security header.
#
# Exit code: 0 = hashes match, 1 = drift.

set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

INDEX_HTML="frontend/index.html"
NGINX_CONFS=("frontend/nginx.conf" "frontend/nginx.docker.conf")

for f in "$INDEX_HTML" "${NGINX_CONFS[@]}"; do
  if [ ! -f "$f" ]; then
    echo "csp_inline_script_hash_check: missing $f" >&2
    exit 1
  fi
done

PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  echo "csp_inline_script_hash_check: no python interpreter found" >&2
  exit 1
fi

# Emits one `sha256-<base64>` per inline <script> (i.e. every <script> with no
# src= attribute), in document order.
#
# The heredoc is read into a variable BEFORE the command substitution rather
# than inlined into it. bash 3.2 — still the default /bin/bash on macOS —
# mis-parses a heredoc nested inside $(...) and chokes on any apostrophe in the
# body, which is a deeply unhelpful "unexpected EOF" at a line number that has
# no apostrophe on it.
PYSRC=''
IFS='' read -r -d '' PYSRC <<'PYEOF'
import base64
import hashlib
import re
import sys

html = open(sys.argv[1], encoding="utf-8").read()
# Inline == no src attribute. CSP hashes cover the element's text content
# exactly as authored, with no surrounding tags.
for body in re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S):
    digest = hashlib.sha256(body.encode("utf-8")).digest()
    print("sha256-" + base64.b64encode(digest).decode())
PYEOF

EXPECTED="$(printf '%s' "$PYSRC" | "$PY" - "$INDEX_HTML")"

if [ -z "$EXPECTED" ]; then
  echo "csp_inline_script_hash_check: found no inline <script> in $INDEX_HTML."
  echo "  If the inline scripts were removed on purpose, drop their hashes from"
  echo "  script-src in ${NGINX_CONFS[*]} and delete this guard."
  exit 1
fi

FAILED=0
for conf in "${NGINX_CONFS[@]}"; do
  while IFS= read -r hash; do
    # Strip CR so Windows CRLF python output still matches LF nginx configs.
    hash="${hash%$'\r'}"
    [ -n "$hash" ] || continue
    if ! grep -qF "'$hash'" "$conf"; then
      echo "FAIL $conf is missing CSP hash '$hash'"
      FAILED=1
    fi
  done <<<"$EXPECTED"
done

if [ "$FAILED" -ne 0 ]; then
  echo ""
  echo "An inline <script> in $INDEX_HTML changed without its CSP hash being"
  echo "updated. The browser will silently refuse to run it in production."
  echo ""
  echo "Current hashes:"
  while IFS= read -r hash; do
    hash="${hash%$'\r'}"
    [ -n "$hash" ] && echo "    '$hash'"
  done <<<"$EXPECTED"
  echo ""
  echo "Paste them into the script-src directive of:"
  for conf in "${NGINX_CONFS[@]}"; do
    echo "    $conf"
  done
  exit 1
fi

exit 0
