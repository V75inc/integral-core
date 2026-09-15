#!/usr/bin/env bash
# Run mypy against backend/app for the pre-commit mypy-backend hook.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=resolve_python.sh
source "$REPO_ROOT/.ci/resolve_python.sh"

PY="$(resolve_python "$REPO_ROOT")"
cd "$REPO_ROOT/backend"

# --follow-imports=skip (not silent): app modules are passed explicitly so
# they are always fully checked; followed third-party imports become Any
# instead of being parsed. This keeps the gate resilient when a local/CI venv
# runs a newer Python than the mypy target (python_version=3.11) and pulls a
# dependency whose stubs use newer syntax — e.g. numpy 2.5's PEP 695 `type`
# aliases, which a 3.11 target rejects with a fatal syntax error.
# Hyphenated-slug App packages live under packages/apps/ (outside app/), so
# they are not on the mypy ``app`` target path. No --exclude needed for them.
exec "$PY" -m mypy app \
  --config-file=pyproject.toml \
  --explicit-package-bases \
  --follow-imports=skip \
  --ignore-missing-imports \
  --no-strict-optional \
  --allow-redefinition \
  --show-error-codes
