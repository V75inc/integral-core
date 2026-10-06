#!/usr/bin/env bash
# Dependency advisory gate — fails only on ACTIONABLE advisories.
#
# Why not a plain `npm audit --audit-level=high` / `pip-audit`:
#
# Both ecosystems currently carry advisories with no released fix. On the
# frontend, react-router carries an RSC-only advisory whose only "fix" is a
# downgrade that reintroduces an applicable XSS. On the backend, ecdsa
# 0.19.2 is flagged by PYSEC-2026-1325 and 0.19.2 IS the latest release; it
# arrives transitively through python-jose. python-jose itself also carries
# CVE-2026-85394 with no fixed release beyond 3.5.0.
#
# (xlsx/SheetJS used to be the headline example here. It is now installed from
# the vendor's CDN at 0.20.3 — which carries the fixes the frozen npm 0.18.5
# never will — so it no longer appears in the audit at all.)
#
# A gate that fails on those is red from the day it lands, and a permanently
# red gate is one people learn to ignore — which is how 11 unnoticed
# advisories accumulated in the first place. So the rule here is:
#
#   fail when an advisory HAS a fix and we have not taken it.
#
# Unfixable advisories are a documented product decision (migrate off the
# package, or accept the risk), not something CI can prompt anyone to fix.
# They are listed below and re-checked on every run: if an upstream fix
# appears, the entry stops matching and the gate starts failing, which is
# exactly the signal we want.

#
# Usage: dependency_audit.sh [backend|frontend|all]
#
# The target is explicit rather than auto-detected. Each CI job installs only
# its own ecosystem's toolchain, and an auto-skip would mean the backend half
# silently never ran inside the Node job — a gate that quietly checks nothing
# is worse than no gate.

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
# PYSEC-2026-1325 (ecdsa): no fixed version exists; ecdsa 0.19.2 is latest.
# Transitive via python-jose. Revisit if python-jose moves off ecdsa or a
# fixed ecdsa ships.
# CVE-2026-85394 (python-jose): DER public-key-as-HMAC bypass; no fixed
# release beyond 3.5.0. Mitigate by pinning algorithms= on every jwt.decode
# (Integral/jvspatial already does). Revisit when upstream ships a fix or
# when we migrate off python-jose.
PIP_IGNORE=(
  --ignore-vuln PYSEC-2026-1325
  --ignore-vuln CVE-2026-85394
)

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
  # jvagent is not published on PyPI, so it is intentionally excluded from
  # this PyPI advisory query; the other exact locked versions remain audited.
  # DELETE this filter when jvagent 0.1.8 final lands on PyPI.
  # backend/requirements.txt used to be that mirror and drifted three RCs
  # behind uv.lock before anyone noticed, so it is gone; uv.lock is the single
  # source of truth and `uv export` renders it in requirements format.
  # Production deps only (no --extra), matching what this gate audited before.
  AUDIT_REQ="$(mktemp)"
  trap 'rm -f "$AUDIT_REQ"' EXIT
  if ! (cd "$REPO_ROOT/backend" && uv export --frozen --no-emit-project \
        --no-hashes) | grep -v '^jvagent==' > "$AUDIT_REQ"; then
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
      --progress-spinner off "${PIP_IGNORE[@]}"; then
    echo "  FAILED: backend audit failed or found an actionable advisory." >&2
    echo "  Fix by bumping the pin, or — if genuinely unfixable — add the ID" >&2
    echo "  to PIP_IGNORE in this script with a reason." >&2
    FAILED=1
  else
    echo "  clean."
  fi
fi
fi

# ---------------------------------------------------------------------------
# Frontend — npm audit, filtered to advisories that HAVE a fix
# ---------------------------------------------------------------------------
# npm audit has no "only fixable" flag, so filter its JSON. `fixAvailable` is
# false when nothing is published, true / an object when there is something to
# take.
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
    RESULT=$(python3 - "$AUDIT_JSON_FILE" "$NPM_STATUS" <<'PY'
import json, sys
from pathlib import Path

try:
    data = json.loads(Path(sys.argv[1]).read_text())
except Exception as exc:
    print(f"PARSE_ERROR: {exc}")
    raise SystemExit(2)
status = int(sys.argv[2])
if not isinstance(data, dict) or not isinstance(data.get("vulnerabilities"), dict):
    print("INVALID_AUDIT_DOCUMENT: missing vulnerabilities object")
    raise SystemExit(2)
if data.get("error") or data.get("message") and status != 0:
    print("AUDIT_ERROR: " + str(data.get("error") or data.get("message")))
    raise SystemExit(2)
if status not in (0, 1):
    print(f"AUDIT_EXIT_ERROR: npm audit exited {status}")
    raise SystemExit(2)

SEVERITIES = {"critical", "high"}

# Advisories accepted by ID, with a reason. Keyed on the GHSA id rather than
# the package name on purpose: a DIFFERENT future advisory against the same
# package still fails the gate. Blanket per-package suppression would silently
# swallow the next real one.
ACCEPTED_ADVISORIES = {
    # RSC Mode CSRF Bypass. The vulnerable code path is react-router in React
    # Server Components mode; this app is a pure client SPA on <BrowserRouter>
    # with no RSC, no SSR and no @react-router/serve, so it is unreachable.
    # npm proposes "fixing" it by moving to 7.11.0 -- a DOWNGRADE that
    # reintroduces GHSA open-redirect/XSS (<=7.17.0), which does apply to a
    # browser router. Taking that would be strictly worse. The real fix is
    # react-router 8.3.0+; revisit when that upgrade is scheduled.
    "GHSA-qwww-vcr4-c8h2": "RSC-only; app is a client SPA. Fix is a downgrade that reintroduces an applicable XSS.",
    # The locked Tailwind 3 build chain reaches braces, for which the reviewed
    # GHSA lists no patched release. npm's suggested fix is a Tailwind 4 major
    # migration; do not silently treat that as a routine patch upgrade.
    "GHSA-vfj7-8cjw-p6xm": "No patched braces release listed; Tailwind 4 is a major migration tracked separately.",
    # postcss-selector-parser quadratic flat-selector parse (moderate). Fixed
    # in 7.1.6, but Tailwind 3 pins the 6.x line; npm's remediation for the
    # high parent entry is Tailwind 4. Ordinary trusted build-time CSS is out
    # of the advisory's reachability scope. Revisit with the Tailwind 4 move.
    "GHSA-rj75-hqrm-r3gf": "postcss-selector-parser fix is 7.x; Tailwind 3 stays on 6.x. Build-time trusted selectors only.",
}

vulns = data.get("vulnerabilities") or {}


def advisory_ids(name, seen=None):
    """GHSA ids for a package, following transitive `via` chains."""
    seen = seen or set()
    if name in seen:
        return set()
    seen.add(name)
    ids = set()
    for via in (vulns.get(name, {}).get("via") or []):
        if isinstance(via, dict):
            url = via.get("url") or ""
            ids.add(url.rsplit("/", 1)[-1])
        elif isinstance(via, str):
            ids |= advisory_ids(via, seen)
    return ids


actionable, accepted = [], []
for name, v in vulns.items():
    if v.get("severity") not in SEVERITIES:
        continue
    ids = advisory_ids(name)
    if ids and ids <= set(ACCEPTED_ADVISORIES):
        accepted.append((name, v.get("severity"), "accepted: " + ",".join(sorted(ids))))
    elif v.get("fixAvailable") is False:
        accepted.append((name, v.get("severity"), "no fix published"))
    else:
        actionable.append((name, v.get("severity")))

for name, sev, why in sorted(accepted):
    print(f"ACCEPTED {sev} {name} ({why})")
for name, sev in sorted(actionable):
    print(f"ACTIONABLE {sev} {name}")
PY
)
    RESULT_STATUS=$?
    rm -f "$AUDIT_JSON_FILE" "$AUDIT_ERR_FILE"
    if [ "$RESULT_STATUS" -ne 0 ]; then
      echo "  FAILED: npm audit returned an error document or invalid output: $RESULT" >&2
      FAILED=1
    else
      printf '%s\n' "$RESULT" | grep '^ACCEPTED' | sed 's/^ACCEPTED /  accepted: /' || true
      if printf '%s\n' "$RESULT" | grep -q '^ACTIONABLE'; then
        printf '%s\n' "$RESULT" | grep '^ACTIONABLE' \
          | sed 's/^ACTIONABLE /  FIXABLE: /' >&2
        echo "  FAILED: run 'npm audit fix' in frontend/ (or bump the dep)." >&2
        FAILED=1
      else
        echo "  clean (no fixable high/critical advisories)."
      fi
    fi
  fi
else
  echo "  FAILED: frontend/node_modules not installed (run npm ci first)" >&2
  FAILED=1
fi
fi

if [ "$FAILED" -ne 0 ]; then
  echo "dependency-audit: FAILED — at least one advisory has a fix available." >&2
  exit 1
fi

echo "dependency-audit: clean."
exit 0
