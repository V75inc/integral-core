#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
python3 "$root/.ci/test_app_boundary_guard.py"
python3 "$root/.ci/app_boundary_guard.py"
