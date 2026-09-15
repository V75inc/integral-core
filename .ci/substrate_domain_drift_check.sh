#!/usr/bin/env bash
# DR-30-03 — I-SUBSTRATE-01 enforcement.
# Forbid bundle-slug / EntryType / Track / V75-token references inside
# substrate code (app/services, app/api, app/models, app/schemas).
# Exclusions for the hook framework + agentive layer + the Phase 30
# generic endpoints follow.
#
# Token strategy:
#   STATIC tokens (curated)   — enforced regardless of length. The 7
#                                V75-era tokens are listed inline.
#   DYNAMIC tokens (harvested) — discovered from
#                                backend/app/profiles/*/profile.yaml and
#                                packages/apps/*/profile.yaml.
#                                Length-filtered (>= 4 chars) to drop
#                                generic short PascalCase names like
#                                "Doc", "Tag", "Bid" that would over-fire.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || { echo "substrate-drift: cannot cd to repo root: $REPO_ROOT" >&2; exit 2; }

PROFILES_ROOT="backend/app/profiles"
APPS_ROOT="packages/apps"
STATIC_TOKENS_FILE="$(mktemp)"
DYNAMIC_TOKENS_FILE="$(mktemp)"
trap 'rm -f "$STATIC_TOKENS_FILE" "$DYNAMIC_TOKENS_FILE"' EXIT

# STATIC tokens — curated V75-era + framework forbids.
# Enforced regardless of length so 3-char tokens like "V75" survive.
cat <<'EOF' >"$STATIC_TOKENS_FILE"
V75
v75_
crm-plus-pm-suite
hr_app
payroll-app
finance
content-factory
EOF

# DYNAMIC tokens — harvested from bundle manifests.
#
# DR-30-03 mandates harvesting:
#   - bundle slugs
#   - Track `name` strings
#   - EntryType `name` strings
#
# An earlier line-grep approach scooped EVERY ``name:`` field in the
# manifest (package name, field labels, view labels, option labels,
# settings labels, …), which produced thousands of false positives
# on common English words like "List", "Email", "Type", "Plan". We
# now use a YAML-aware Python helper so only Track + EntryType names
# enter the dynamic forbid set.
#
# Slugs continue to come from a simple line-grep — they appear at
# only one level in the manifest (top-level + nested bundle slugs)
# and are guaranteed to be kebab-case identifiers.
if [ -d "$PROFILES_ROOT" ] || [ -d "$APPS_ROOT" ]; then
  # Generic-word slugs collide with substrate English vocabulary as
  # substrings (slug "produce" matches "produced" in docstrings; "pulse"
  # matches the plan-rollup walker's prose). The gate cannot meaningfully
  # enforce these — this mirrors the ``_GENERIC_WORDS`` name filter for
  # slugs. A real domain leak surfaces as a distinctive kebab-case slug.
  _GENERIC_SLUGS='produce|pulse'
  for _harvest_root in "$PROFILES_ROOT" "$APPS_ROOT"; do
    [ -d "$_harvest_root" ] || continue
    grep -rh -E '^[[:space:]]*slug:' "$_harvest_root" 2>/dev/null \
      | sed -E 's/^[[:space:]]*slug:[[:space:]]*//' \
      | sed "s/['\"]//g" \
      | grep -vixE "$_GENERIC_SLUGS" \
      | sort -u >>"$DYNAMIC_TOKENS_FILE"
  done
  # Prefer the backend venv's python (PyYAML installed) over system
  # python so the harvest works on developer laptops without polluting
  # the system site-packages. resolve_python covers Windows (.venv) too.
  # shellcheck source=resolve_python.sh
  source "$REPO_ROOT/.ci/resolve_python.sh"
  PYBIN="$(resolve_python "$REPO_ROOT")" || exit 1
  for _harvest_root in "$PROFILES_ROOT" "$APPS_ROOT"; do
    [ -d "$_harvest_root" ] || continue
    "$PYBIN" - "$_harvest_root" >>"$DYNAMIC_TOKENS_FILE" <<'PY'
"""Extract Track + EntryType ``name`` strings from every bundle manifest.

Walks all ``profile.yaml`` files under the given root and emits one name
per line. Track-scope and app-scope manifests are both supported.
"""
import sys
from pathlib import Path

try:
    import yaml  # type: ignore
except Exception:
    sys.exit(0)  # PyYAML missing — fall back to slug-only harvest.

root = Path(sys.argv[1])
emitted: set[str] = set()

# Generic English nouns that bundles routinely pick for their Track or
# EntryType names. These collide with substrate code's legitimate use
# of the same words (asyncio.Task, ChangeEvent, source identifier,
# Project / Account / Pipeline as generic vocabulary, …). The drift
# gate cannot meaningfully enforce I-SUBSTRATE-01 against these — the
# substrate would have to rename half its variables. They are filtered
# out of the dynamic harvest; a real domain leak would manifest as a
# longer / more-specific token (e.g. "Project Proposal",
# "Email Thread", "Performance Record") which still flows through.
_GENERIC_WORDS = {
    "Account",
    "Activity",
    "Artifact",
    "Artifacts",
    "Bug",
    "Bug Tracking",
    "Catalog",
    "Communications",
    "Contact",
    "Contacts",
    "Decision",
    "Employee",
    "Employees",
    "Event",
    "Events",
    "Expenses",
    "Heuristic",
    "Idea",
    "Ideas",
    "Identity",
    "Interaction",
    "Item",
    "Metrics",
    "Opportunities",
    "Opportunity",
    "Page",
    "Pages",
    "Performance",
    "Person",
    "Pipeline",
    "Product",
    "Plan",
    "Plans",
    "Project",
    "Projects",
    "Role",
    "Source",
    "Sources",
    "Status Report",
    "Status Reports",
    "Stream",
    "Task",
    "Tasks",
    "Template",
    "Templates",
}


def _emit(value: object) -> None:
    if isinstance(value, str):
        v = value.strip()
        if not v or not v[0].isupper():
            return
        if v in _GENERIC_WORDS:
            return
        emitted.add(v)


def _walk_tracks(tracks: object) -> None:
    if not isinstance(tracks, list):
        return
    for t in tracks:
        if not isinstance(t, dict):
            continue
        _emit(t.get("name"))
        for et in (t.get("entry_types") or []):
            if isinstance(et, dict):
                _emit(et.get("name"))


for path in root.rglob("profile.yaml"):
    try:
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        continue
    if not isinstance(manifest, dict):
        continue
    # track-scope: top-level entry_types[]
    for et in (manifest.get("entry_types") or []):
        if isinstance(et, dict):
            _emit(et.get("name"))
    # app-scope: app.tracks[].name + app.tracks[].entry_types[].name
    app_block = manifest.get("app") or {}
    if isinstance(app_block, dict):
        _walk_tracks(app_block.get("tracks"))

for name in sorted(emitted):
    print(name)
PY
  done
fi

# De-dupe + strip empty lines from dynamic file.
sort -u -o "$DYNAMIC_TOKENS_FILE" "$DYNAMIC_TOKENS_FILE"
sed -i.bak '/^$/d' "$DYNAMIC_TOKENS_FILE" && rm -f "$DYNAMIC_TOKENS_FILE.bak"

# Subtract STATIC tokens from DYNAMIC to prevent double-reporting:
# a leak line containing "finance" should fire once (as static), not
# twice (static + dynamic).
if [ -s "$DYNAMIC_TOKENS_FILE" ] && [ -s "$STATIC_TOKENS_FILE" ]; then
  grep -vxFf "$STATIC_TOKENS_FILE" "$DYNAMIC_TOKENS_FILE" > "$DYNAMIC_TOKENS_FILE.tmp" || true
  mv "$DYNAMIC_TOKENS_FILE.tmp" "$DYNAMIC_TOKENS_FILE"
fi

# Scope: substrate paths only.
SCOPE_PATHS=(
  "backend/app/services"
  "backend/app/api"
  "backend/app/models"
  "backend/app/schemas"
)

# Exclusions — the hook framework + agentive + the migrated endpoints
# are bundle-aware on purpose (they dispatch generically).
EXCLUDE=(
  "--exclude-dir=__pycache__"
  "--exclude-dir=agentive"
  "--exclude-dir=hooks"
  "--exclude=entries_transform.py"
  "--exclude=entries_public_share.py"
  "--exclude=entries_precompute.py"
  "--exclude=tools.py"
  "--exclude=apps_skills.py"
  "--exclude=entry_relations.py"
  "--exclude=sync_runtime.py"
)

# Allowlist — substring match against grep hit; reason comment required.
ALLOWLIST=".ci/substrate_drift_allowlist.txt"
allow_match() {
  local hit="$1"
  [ -f "$ALLOWLIST" ] || return 1
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"
    line="$(echo -n "$line" | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')"
    [ -z "$line" ] && continue
    if echo "$hit" | grep -F -q "$line"; then
      return 0
    fi
  done <"$ALLOWLIST"
  return 1
}

violations=0

# STATIC pass — no length filter.
while IFS= read -r token || [ -n "$token" ]; do
  [ -z "$token" ] && continue
  hits="$(grep -rn "${EXCLUDE[@]}" -F -- "$token" "${SCOPE_PATHS[@]}" 2>/dev/null || true)"
  [ -z "$hits" ] && continue
  while IFS= read -r hit; do
    [ -z "$hit" ] && continue
    if allow_match "$hit"; then continue; fi
    echo "substrate-drift: forbidden token \"$token\" → $hit"
    violations=$((violations + 1))
  done <<<"$hits"
done <"$STATIC_TOKENS_FILE"

# DYNAMIC pass — length filter applies to suppress generic short tokens.
while IFS= read -r token || [ -n "$token" ]; do
  [ -z "$token" ] && continue
  if [ "${#token}" -lt 4 ]; then continue; fi
  hits="$(grep -rn "${EXCLUDE[@]}" -F -- "$token" "${SCOPE_PATHS[@]}" 2>/dev/null || true)"
  [ -z "$hits" ] && continue
  while IFS= read -r hit; do
    [ -z "$hit" ] && continue
    if allow_match "$hit"; then continue; fi
    echo "substrate-drift: forbidden token \"$token\" → $hit"
    violations=$((violations + 1))
  done <<<"$hits"
done <"$DYNAMIC_TOKENS_FILE"

if [ "$violations" -gt 0 ]; then
  echo ""
  echo "substrate-drift: $violations violation(s). See DR-30-03 / I-SUBSTRATE-01."
  echo "  Resolve by: (1) move domain code to a bundle hook/tool, OR"
  echo "  (2) add an allowlist line in $ALLOWLIST with a # reason: comment."
  exit 1
fi

echo "substrate-drift: clean."
exit 0
