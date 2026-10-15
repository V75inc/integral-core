#!/usr/bin/env bash
# Dependency advisory gate.
#
# Backend: audit the frozen lock export with no advisory suppressions. Core's
# runtime and fallback test tokens use PyJWT; obsolete python-jose and ecdsa
# dependencies have been removed rather than retaining permanent exceptions.
# Frontend: apply the high/critical policy and reviewed conditions below.
#
# Usage: dependency_audit.sh [backend|frontend|all]
# Each CI job explicitly selects its installed ecosystem. An unavailable
# auditor or invalid audit response is a failure, never a silent skip.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${1:-all}"
FAILED=0

case "$TARGET" in
  backend | frontend | all) ;;
  *)
    echo "usage: $(basename "$0") [backend|frontend|all]" >&2
    exit 2
    ;;
esac

# ---------------------------------------------------------------------------
# Backend — pip-audit
# ---------------------------------------------------------------------------
if [ "$TARGET" = "backend" ] || [ "$TARGET" = "all" ]; then
echo "dependency-audit: backend (pip-audit)..."
if command -v pip-audit >/dev/null 2>&1; then
  PIP_AUDIT=(pip-audit --no-deps --disable-pip)
elif command -v uvx >/dev/null 2>&1; then
  PIP_AUDIT=(uvx pip-audit --no-deps --disable-pip)
else
  # Hard failure when this target was asked for: a silent skip would let the
  # backend go unaudited forever without anyone noticing.
  echo "  FAILED: neither pip-audit nor uvx is available" >&2
  FAILED=1
  PIP_AUDIT=()
fi

if [ ${#PIP_AUDIT[@]} -gt 0 ]; then
  # Audit the frozen, fully pinned lock export directly. --no-deps avoids
  # re-resolving it and --disable-pip avoids creating a temporary venv through
  # ensurepip; both made the audit depend on resolver/index behavior rather
  # than the package versions we actually ship.
  #
  # The export comes from uv.lock, not a hand-maintained requirements mirror.
  # backend/requirements.txt used to be that mirror and drifted three RCs
  # behind uv.lock before anyone noticed, so it is gone; uv.lock is the single
  # source of truth and `uv export` renders it in requirements format.
  # Production deps only (no --extra), matching what this gate audited before.
  AUDIT_REQ="$(mktemp)"
  trap 'rm -f "$AUDIT_REQ"' EXIT
  if ! (cd "$REPO_ROOT/backend" && uv export --frozen --no-emit-project \
        --no-hashes) > "$AUDIT_REQ"; then
    echo "  FAILED: could not export backend/uv.lock (is uv installed?)." >&2
    FAILED=1
  fi
  if [ ! -s "$AUDIT_REQ" ]; then
    # An empty export would make pip-audit trivially "pass" having checked
    # nothing — the exact silent-no-op this gate's header warns about.
    echo "  FAILED: uv export produced no requirements to audit." >&2
    FAILED=1
  fi
  if ! "${PIP_AUDIT[@]}" -r "$AUDIT_REQ" \
      --progress-spinner off; then
    echo "  FAILED: backend audit failed or reported an advisory." >&2
    echo "  Upgrade or remove the affected dependency; inspect the complete audit." >&2
    FAILED=1
  else
    echo "  clean."
  fi
fi
fi

# ---------------------------------------------------------------------------
# Frontend — complete inventory and reviewed high/critical conditions
# ---------------------------------------------------------------------------
# Unknown high/critical findings fail even without a published fix. Reviewed
# exceptions bind the installed lock graph, remediation metadata and expiry.
if [ "$TARGET" = "frontend" ] || [ "$TARGET" = "all" ]; then
echo "dependency-audit: frontend (npm audit)..."
if [ -d "$REPO_ROOT/frontend/node_modules" ]; then
  AUDIT_JSON_FILE="$(mktemp)"
  AUDIT_ERR_FILE="$(mktemp)"
  (cd "$REPO_ROOT/frontend" && npm audit --json >"$AUDIT_JSON_FILE" 2>"$AUDIT_ERR_FILE")
  NPM_STATUS=$?
  if [ -s "$AUDIT_ERR_FILE" ]; then
    cat "$AUDIT_ERR_FILE" >&2
  fi
  if [ ! -s "$AUDIT_JSON_FILE" ]; then
    echo "  FAILED: npm audit returned no JSON (exit=$NPM_STATUS)." >&2
    FAILED=1
  else
    RESULT=$(python3 "$REPO_ROOT/.ci/frontend_dependency_policy.py" \
      "$AUDIT_JSON_FILE" "$NPM_STATUS" "$REPO_ROOT/frontend/package-lock.json")
    RESULT_STATUS=$?
    rm -f "$AUDIT_JSON_FILE" "$AUDIT_ERR_FILE"
    if [ "$RESULT_STATUS" -ne 0 ]; then
      echo "  FAILED: npm audit returned an error document or invalid output: $RESULT" >&2
      FAILED=1
    else
      printf '%s\n' "$RESULT" | grep '^REVIEW_CONTEXT ' || true
      printf '%s\n' "$RESULT" | grep '^NOTICE' || true
      printf '%s\n' "$RESULT" | grep '^ACCEPTED' | sed 's/^ACCEPTED /  accepted: /' || true
      if printf '%s\n' "$RESULT" | grep -q '^ACTIONABLE'; then
        printf '%s\n' "$RESULT" | grep '^ACTIONABLE' \
          | sed 's/^ACTIONABLE /  ACTIONABLE: /' >&2
        echo "  FAILED: review the affected dependency and remediation; do not blindly downgrade." >&2
        FAILED=1
      else
        echo "  passed (no unreviewed high/critical advisories)."
      fi
    fi
  fi
else
  echo "  FAILED: frontend/node_modules not installed (run npm ci first)" >&2
  FAILED=1
fi
fi

if [ "$FAILED" -ne 0 ]; then
  echo "dependency-audit: FAILED — an audit failed or a finding requires action." >&2
  exit 1
fi

echo "dependency-audit: passed."
exit 0
