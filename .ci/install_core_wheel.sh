#!/usr/bin/env bash
# Install a built integral-core wheel plus the dependencies named in its
# metadata. jvagent's pre-release is on TestPyPI only. Fetch that one wheel
# from TestPyPI and expose it with --find-links. An unscoped extra index is
# not used: TestPyPI has published a broken sdist under the name fastapi.
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: install_core_wheel.sh <python> <wheel>" >&2
  exit 2
fi

PYTHON="$1"
WHEEL="$2"
LINKS="$(mktemp -d "${TMPDIR:-/tmp}/integral-jvagent-links.XXXXXX")"
trap 'rm -rf "$LINKS"' EXIT

META="$(unzip -Z1 "$WHEEL" | awk '/\.dist-info\/METADATA$/{print; exit}')"
test -n "$META"
SPEC="$(unzip -p "$WHEEL" "$META" | awk -F': ' '/^Requires-Dist: jvagent==/{print $2; exit}')"
test -n "$SPEC"

ROOT="$(cd "$(dirname "$0")" && pwd)"
"$ROOT/fetch_jvagent_wheel.sh" "$SPEC" "$LINKS" >/dev/null
uv pip install --python "$PYTHON" --find-links "$LINKS" "$WHEEL"
