#!/usr/bin/env bash
# Skill SKILL.md compliance guard — see docs/backend/skill-format-standard.md

set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"
# shellcheck source=resolve_python.sh
source "$REPO_ROOT/.ci/resolve_python.sh"

PY="$(resolve_python "$REPO_ROOT")"
cd "$REPO_ROOT/backend"
"$PY" -m pytest tests/test_skill_compliance.py -q
