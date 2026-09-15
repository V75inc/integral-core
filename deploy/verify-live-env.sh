#!/usr/bin/env bash
# Verify a LIVE Integral deployment from the outside — no secrets read, no
# state touched. Converts the post-deploy operator checklist into exit codes.
#
# Every check here is a trap that has actually fired (or been caught in
# review) in this repo, and every one shares the same failure shape: the
# stack's healthchecks stay green while the deployment is broken for users.
#
#   1. DOMAIN serves the SPA and sends the security headers nginx.conf owns.
#   2. The CSP's connect-src names the SAME origin the SPA bundle will call.
#      A same-origin VITE_API_URL against the split-host stack yields an SPA
#      calling a static nginx that cannot proxy — this is the check that
#      catches it from outside.
#   3. DOMAIN_API answers /health (split-host stacks only).
#   4. The API refuses unauthenticated traffic properly (401 envelope, not a
#      5xx or an nginx error page — which would mean routing is wrong).
#
# Usage:
#   deploy/verify-live-env.sh <DOMAIN> [DOMAIN_API]
#     e.g. deploy/verify-live-env.sh integral.example.com api.integral.example.com
#   Omit DOMAIN_API for the same-origin (docker-compose.prod.yml) topology.
#
# What this CANNOT see from outside, and how to check it over SSH:
#
#   * JVAGENT_UPDATE_MODE / observation budgets. Under `merge`, agent.yaml
#     budget edits are ignored and boot logs a WARNING naming the stale keys:
#         ssh <host> docker service logs <stack>_api 2>&1 | grep -i 'observation'
#     No warning after a fresh deploy = budgets landed. Warning present = run
#     the one-shot `source` boot (DEPLOY.md § Observation budgets) and re-check.
#
#   * The JWT secret's value. Post-#80 the boot guard REFUSES placeholder
#     keys, so "the api service is running at all" is itself the check:
#         ssh <host> docker service ps <stack>_api
#     A crash-loop right after deploying new env = read the logs; the guard
#     prints exactly what it rejected and why.

set -euo pipefail

DOMAIN="${1:?usage: verify-live-env.sh <DOMAIN> [DOMAIN_API]}"
DOMAIN_API="${2:-}"

FAIL=0
note() { printf '%s\n' "$*"; }
ok()   { printf 'PASS  %s\n' "$*"; }
bad()  { printf 'FAIL  %s\n' "$*"; FAIL=1; }

# --- 1. SPA is up and serving the hardened nginx ---------------------------
SPA_HEADERS="$(curl -sSI --max-time 15 "https://${DOMAIN}/" || true)"
if [ -z "${SPA_HEADERS}" ]; then
  bad "https://${DOMAIN}/ did not answer at all"
  exit 1
fi
printf '%s' "${SPA_HEADERS}" | grep -qi '^HTTP/.* 200' \
  && ok "SPA answers 200 on https://${DOMAIN}/" \
  || bad "SPA did not answer 200 on https://${DOMAIN}/"

for header in content-security-policy strict-transport-security \
              x-content-type-options x-frame-options; do
  printf '%s' "${SPA_HEADERS}" | grep -qi "^${header}:" \
    && ok "SPA sends ${header}" \
    || bad "SPA is missing ${header} — is the deployed WEB_IMAGE stale, or serving nginx.docker.conf?"
done

# --- 2. CSP connect-src agrees with the topology ---------------------------
CSP="$(printf '%s' "${SPA_HEADERS}" | grep -i '^content-security-policy:' || true)"
if printf '%s' "${CSP}" | grep -q '\${CSP_EXTRA_CONNECT}'; then
  bad "CSP contains the LITERAL \${CSP_EXTRA_CONNECT} — the envsh defaulting hook did not run (see DEPLOY.md; this was the 0644 .envsh bug)"
fi
if [ -n "${DOMAIN_API}" ]; then
  printf '%s' "${CSP}" | grep -q "https://${DOMAIN_API}" \
    && ok "CSP connect-src names https://${DOMAIN_API} — the bundle was built against the split-host API" \
    || bad "CSP connect-src does NOT name https://${DOMAIN_API}. The SPA was built with a same-origin VITE_API_URL against the split-host stack: every XHR will fail while healthchecks stay green. Rebuild WEB_IMAGE with VITE_API_URL=https://${DOMAIN_API}."
else
  note "note  no DOMAIN_API given — same-origin topology assumed; connect-src should cover 'self'"
fi

# --- 3. API health ----------------------------------------------------------
if [ -n "${DOMAIN_API}" ]; then
  API_ORIGIN="https://${DOMAIN_API}"
else
  API_ORIGIN="https://${DOMAIN}"
fi
HEALTH_CODE="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "${API_ORIGIN}/health" || echo 000)"
[ "${HEALTH_CODE}" = "200" ] \
  && ok "API /health is 200 on ${API_ORIGIN}" \
  || bad "API /health returned ${HEALTH_CODE} on ${API_ORIGIN} — DOMAIN_API unset in the live env, or Traefik has no router for it"

# --- 4. API auth surface behaves like the API, not like nginx ---------------
ME_BODY="$(curl -sS --max-time 15 "${API_ORIGIN}/api/auth/me" || true)"
ME_CODE="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "${API_ORIGIN}/api/auth/me" || echo 000)"
if [ "${ME_CODE}" = "401" ] && printf '%s' "${ME_BODY}" | grep -q 'error_code'; then
  ok "unauthenticated /api/auth/me → 401 with the canonical envelope"
elif printf '%s' "${ME_BODY}" | grep -qi '<html'; then
  bad "/api/auth/me returned an HTML page — requests are landing on the static SPA nginx, not the API (routing/VITE_API_URL mismatch)"
else
  bad "unauthenticated /api/auth/me → ${ME_CODE} (expected 401 + JSON envelope)"
fi

echo
if [ "${FAIL}" = "0" ]; then
  echo "All externally-verifiable checks passed. SSH-only checks are listed in the header of this script."
else
  echo "FAILURES above. Each message names the repo-side cause and fix."
  exit 1
fi
