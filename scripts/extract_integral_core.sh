#!/usr/bin/env bash
# Build a clean integral-core tree for the public OSS initial commit.
# Usage:
#   ./scripts/extract_integral_core.sh [output_dir]
# Then: cd output_dir && git init && git remote add origin <PUBLIC_URL> && ...

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$REPO_ROOT/../integral-core-extract}"

rm -rf "$OUT"
mkdir -p "$OUT"

rsync -a --delete \
  --exclude '.git' \
  --exclude 'packages/apps' \
  --exclude 'frontend/src/views/productManifests' \
  --exclude 'scripts/seed_product.py' \
  --exclude '**/__pycache__' \
  --exclude '**/.venv' \
  --exclude 'node_modules' \
  --exclude 'backend/test_integral*' \
  --exclude 'backend/.pytest_cache' \
  --exclude 'frontend/dist' \
  "$REPO_ROOT/" "$OUT/"

# Ensure Core profiles seeds-only (defense in depth)
if [ -d "$OUT/backend/app/profiles" ]; then
  find "$OUT/backend/app/profiles" -mindepth 1 -maxdepth 1 \( -type d -o -type f \) \
    ! -name 'agent-scratch' \
    ! -name '__init__.py' \
    ! -name '__pycache__' \
    -exec rm -rf {} +
fi

# Drop commercial product FE registration from Core views index
if [ -f "$OUT/frontend/src/views/index.ts" ]; then
  cat >"$OUT/frontend/src/views/index.ts" <<'EOF'
import { registerDiscoveredWidgets } from './manifests/auto';

registerDiscoveredWidgets();

export {
  ViewRenderer,
  ViewSelector,
  getWidget,
  listWidgets,
  registerWidget,
  getEnabledWidgetTypes,
  getEnabledWidgetRegistrations,
  listWidgetCapabilities,
} from './registry';
export type {
  ViewWidgetProps,
  WidgetMeta,
  WidgetRegistration,
  WidgetCapabilityDescriptor,
} from './types';
EOF
fi

# NOTICE for ancestry
cat >"$OUT/NOTICE" <<EOF
This tree was extracted from the Integral monorepo for the open integral-core
release. Commercial Apps live in a separate private repository and are not
bundled here. See docs/product/INTEGRAL_CORE_EXTRACT.md.
EOF

# Make Core Docker the documented default (comment banner)
if [ -f "$OUT/backend/Dockerfile" ]; then
  echo "# Default OSS image: docker build --target core -f backend/Dockerfile ." >>"$OUT/backend/Dockerfile"
fi

echo "extract ready: $OUT"
echo "Next: create public repo, then:"
echo "  cd $OUT && git init && git add -A && git commit -m 'chore: initial integral-core v0.1.0'"
echo "  git remote add origin <PUBLIC_URL> && git push -u origin HEAD && git tag v0.1.0 && git push origin v0.1.0"
