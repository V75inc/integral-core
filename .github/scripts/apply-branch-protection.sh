#!/usr/bin/env bash
# Apply branch protection from .github/branch-protection.json
# Usage: apply-branch-protection.sh [--solo]
#   --solo  set required_approving_review_count=0 (merge without bot approval)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CONFIG="$ROOT/.github/branch-protection.json"
REPO="${GITHUB_REPOSITORY:-V75inc/integral-core}"
SOLO=false
if [[ "${1:-}" == "--solo" ]]; then
  SOLO=true
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI required" >&2
  exit 1
fi

cd "$ROOT"
protection=$(SOLO_FLAG="$SOLO" python3 - <<'PY'
import json, os, pathlib
cfg = json.loads(pathlib.Path(".github/branch-protection.json").read_text())
solo = os.environ.get("SOLO_FLAG") == "true"
body = {
    "required_status_checks": None,
    "enforce_admins": cfg["protection"]["enforce_admins"],
    "required_pull_request_reviews": dict(cfg["protection"]["required_pull_request_reviews"]),
    "restrictions": None,
    "required_linear_history": cfg["protection"]["required_linear_history"],
    "allow_force_pushes": cfg["protection"]["allow_force_pushes"],
    "allow_deletions": cfg["protection"]["allow_deletions"],
    "block_creations": False,
    "required_conversation_resolution": cfg["protection"]["required_conversation_resolution"],
    "lock_branch": False,
    "allow_fork_syncing": False,
}
if solo:
    body["required_pull_request_reviews"]["required_approving_review_count"] = cfg["solo_maintenance"]["required_approving_review_count"]
print(json.dumps(body))
PY
)

branches=$(python3 -c "import json, pathlib; print(' '.join(json.loads(pathlib.Path('.github/branch-protection.json').read_text())['branches']))")

for branch in $branches; do
  echo "Applying protection to ${REPO}@${branch} (solo=${SOLO})"
  if ! gh api --method PUT "repos/${REPO}/branches/${branch}/protection" --input - <<<"$protection"; then
    echo "warning: skipped missing or inaccessible branch ${branch}" >&2
  fi
done

echo "Done."
