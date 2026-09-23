#!/usr/bin/env bash
# Build the React workspace with same-origin /api and copy it into the
# package tree so `python -m build` ships it inside the wheel.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root/frontend"
npm ci
VITE_API_URL= npm run build
dest="$root/backend/app/web/static"
rm -rf "$dest"
mkdir -p "$dest"
cp -R dist/. "$dest/"
test -f "$dest/index.html"
