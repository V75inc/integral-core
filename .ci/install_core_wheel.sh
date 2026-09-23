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

python3 - "$SPEC" "$LINKS" <<'PY'
import json, sys, urllib.request
from pathlib import Path

spec, dest = sys.argv[1], Path(sys.argv[2])
name, version = spec.split("==", 1)
url = f"https://test.pypi.org/pypi/{name}/{version}/json"
with urllib.request.urlopen(url, timeout=60) as resp:
    meta = json.load(resp)
wheels = [u for u in meta["urls"] if u["packagetype"] == "bdist_wheel"]
if not wheels:
    raise SystemExit(f"no wheel for {spec} on TestPyPI")
chosen = wheels[0]
out = dest / chosen["filename"]
urllib.request.urlretrieve(chosen["url"], out)
print(out)
PY
uv pip install --python "$PYTHON" --find-links "$LINKS" "$WHEEL"
