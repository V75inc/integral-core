#!/usr/bin/env bash
# Package only portable native resident skills.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
dest="$root/backend/app/resident_harness"
rm -rf "$dest" "$root/backend/build"
mkdir -p "$dest/skills"
tar -C "$root/agent/skills" --exclude '__pycache__' --exclude '*.pyc' -cf - . | tar -C "$dest/skills" -xf -
test -f "$dest/skills/integral-scaffold/SKILL.md"
