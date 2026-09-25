#!/usr/bin/env bash
# Download one jvagent wheel from TestPyPI into DEST.
# Usage: fetch_jvagent_wheel.sh jvagent==0.1.8rc17 /path/to/dir
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: fetch_jvagent_wheel.sh <name==version> <dest-dir>" >&2
  exit 2
fi

python3 - "$1" "$2" <<'PY'
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
pure = [u for u in wheels if "py3-none-any" in u["filename"]]
chosen = pure[0] if pure else wheels[0]
out = dest / chosen["filename"]
urllib.request.urlretrieve(chosen["url"], out)
print(out)
PY
