#!/usr/bin/env python3
"""Copy EXAMPLE -> DEST if DEST is missing, then replace placeholder secrets.

Usage: python bootstrap_env.py DEST EXAMPLE
"""

from __future__ import annotations

import base64
import re
import secrets
import shutil
import sys
from pathlib import Path

MARKERS = (
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
    return any(m in low for m in MARKERS)


def set_key(body: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.M)
    line = f"{key}={value}"
    if pattern.search(body):
        return pattern.sub(line, body, count=1)
    if body and not body.endswith("\n"):
        body += "\n"
    return body + line + "\n"


def bootstrap(dest_path: Path, example_path: Path) -> None:
    if not example_path.is_file():
        sys.stderr.write(f"bootstrap_env: missing example {example_path}\n")
        sys.exit(1)

    if not dest_path.is_file():
        shutil.copyfile(example_path, dest_path)
        print(f"bootstrap_env: wrote {dest_path} from {example_path.name}")

    text = dest_path.read_text(encoding="utf-8")
    changed = False

    m = re.search(r"^JVSPATIAL_JWT_SECRET_KEY=(.*)$", text, re.M)
    if m is None or is_placeholder(m.group(1)):
        jwt = secrets.token_hex(32)
        text = set_key(text, "JVSPATIAL_JWT_SECRET_KEY", jwt)
        changed = True

    m = re.search(r"^INTEGRAL_CREDENTIAL_ENC_KEY=(.*)$", text, re.M)
    if m is None or is_placeholder(m.group(1)):
        cred = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
        text = set_key(text, "INTEGRAL_CREDENTIAL_ENC_KEY", cred)
        changed = True

    if changed:
        dest_path.write_text(text, encoding="utf-8")
        print(f"bootstrap_env: filled secrets in {dest_path}")
    else:
        print(f"bootstrap_env: secrets already set in {dest_path}")


def main() -> None:
    if len(sys.argv) != 3:
        sys.stderr.write("usage: bootstrap_env.py DEST EXAMPLE\n")
        sys.exit(1)
    dest = Path(sys.argv[1])
    example = Path(sys.argv[2])
    bootstrap(dest, example)


if __name__ == "__main__":
    main()
