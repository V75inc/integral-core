#!/usr/bin/env bash
# Copy EXAMPLE → DEST if DEST is missing, then replace placeholder secrets.
# Usage: bootstrap_env.sh DEST EXAMPLE
set -euo pipefail

DEST="${1:?usage: bootstrap_env.sh DEST EXAMPLE}"
EXAMPLE="${2:?usage: bootstrap_env.sh DEST EXAMPLE}"

if [[ ! -f "$EXAMPLE" ]]; then
  echo "bootstrap_env: missing example $EXAMPLE" >&2
  exit 1
fi

if [[ ! -f "$DEST" ]]; then
  cp "$EXAMPLE" "$DEST"
  echo "bootstrap_env: wrote $DEST from $(basename "$EXAMPLE")"
fi

python3 - "$DEST" <<'PY'
import base64
import re
import secrets
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
markers = (
    "replace-me",
    "replace-with",
    "change-me",
    "changeme",
    "change-in-production",
    "your-secret",
    "placeholder",
)


def is_placeholder(val: str) -> bool:
    v = val.strip().strip('"').strip("'")
    if len(v) < 32:
        return True
    low = v.lower()
    return any(m in low for m in markers)


def set_key(body: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.M)
    line = f"{key}={value}"
    if pattern.search(body):
        return pattern.sub(line, body, count=1)
    if body and not body.endswith("\n"):
        body += "\n"
    return body + line + "\n"


changed = False
jwt = None
m = re.search(r"^JVSPATIAL_JWT_SECRET_KEY=(.*)$", text, re.M)
if m is None or is_placeholder(m.group(1)):
    jwt = secrets.token_hex(32)
    text = set_key(text, "JVSPATIAL_JWT_SECRET_KEY", jwt)
    changed = True

cred = None
m = re.search(r"^INTEGRAL_CREDENTIAL_ENC_KEY=(.*)$", text, re.M)
if m is None or is_placeholder(m.group(1)):
    cred = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    text = set_key(text, "INTEGRAL_CREDENTIAL_ENC_KEY", cred)
    changed = True

if changed:
    path.write_text(text, encoding="utf-8")
    print(f"bootstrap_env: filled secrets in {path}")
else:
    print(f"bootstrap_env: secrets already set in {path}")
PY
