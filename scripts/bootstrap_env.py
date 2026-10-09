#!/usr/bin/env python3
"""Copy EXAMPLE -> DEST if DEST is missing, then replace placeholder secrets.

Usage: python bootstrap_env.py DEST EXAMPLE
"""

from __future__ import annotations

import base64
import binascii
import os
import re
import secrets
import shutil
import sys
import tempfile
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
    if not v:
        return True
    low = v.lower()
    return any(m in low for m in MARKERS)


def env_value(raw: str) -> str:
    """Validate the value dotenv actually loads, without exposing its contents."""
    value = raw.strip()
    if value.startswith(('"', "'")):
        end = value.find(value[0], 1)
        trailing = value[end + 1 :].strip()
        if end < 0 or (trailing and not trailing.startswith("#")):
            raise ValueError(
                "Malformed quoted secret assignment; correct it before bootstrap."
            )
        return value[1:end]
    return re.split(r"\s+#", value, maxsplit=1)[0].strip()


def is_credential_key(value: str) -> bool:
    """Accept the same 32-byte base64, hex and raw forms as Core encryption."""
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
        if len(decoded) == 32:
            return True
    except (ValueError, binascii.Error):
        pass
    try:
        if len(bytes.fromhex(value)) == 32:
            return True
    except ValueError:
        pass
    return len(value.encode("utf-8")) == 32


def set_key(body: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^[ \t]*(?:export\s+)?{re.escape(key)}\s*=.*$", re.M)
    line = f"{key}={value}"
    if pattern.search(body):
        return pattern.sub(line, body, count=1)
    commented = re.compile(rf"^[ \t]*#\s*{re.escape(key)}\s*=.*$", re.M)
    if commented.search(body):
        return commented.sub(line, body, count=1)
    if body and not body.endswith("\n"):
        body += "\n"
    return body + line + "\n"


def is_fernet_key(val: str) -> bool:
    """Return whether val is a canonical URL-safe Fernet key."""
    candidate = val.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{43}=", candidate) is None:
        return False
    try:
        decoded = base64.urlsafe_b64decode(candidate)
    except (ValueError, binascii.Error):
        return False
    return len(decoded) == 32


def bootstrap(dest_path: Path, example_path: Path) -> None:
    if not example_path.is_file():
        sys.stderr.write(f"bootstrap_env: missing example {example_path}\n")
        sys.exit(1)

    if not dest_path.is_file():
        shutil.copyfile(example_path, dest_path)
        dest_path.chmod(0o600)
        print(f"bootstrap_env: wrote {dest_path} from {example_path.name}")

    text = dest_path.read_text(encoding="utf-8")
    changed = False

    # Validate all existing secrets before writing anything. A malformed key
    # may belong to existing encrypted data; bootstrap must never rotate it.
    specs = (
        (
            "JVSPATIAL_JWT_SECRET_KEY",
            lambda value: len(value) >= 32,
            lambda: secrets.token_hex(32),
        ),
        (
            "INTEGRAL_CREDENTIAL_ENC_KEY",
            is_credential_key,
            lambda: base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
        ),
        (
            "JVSPATIAL_OAUTH_KEY_ENCRYPTION_KEY",
            is_fernet_key,
            lambda: base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii"),
        ),
    )
    for key, validate, generate in specs:
        assignments = re.findall(
            rf"^[ \t]*(?:export\s+)?{re.escape(key)}\s*=(.*)$", text, re.M
        )
        if len(assignments) > 1:
            raise ValueError(
                f"{key} has duplicate assignments; retain one original key before bootstrap. Existing keys were not changed."
            )
        value = env_value(assignments[0]) if assignments else ""
        if is_placeholder(value):
            text = set_key(text, key, generate())
            changed = True
        elif not validate(value):
            raise ValueError(
                f"{key} is invalid. Existing keys were not changed. Restore the "
                "original key from your backup or secret manager; do not replace "
                "it if this installation already contains encrypted data."
            )

    if changed:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=dest_path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
        os.replace(temporary, dest_path)
        print(f"bootstrap_env: filled secrets in {dest_path}")
    else:
        print(f"bootstrap_env: secrets already set in {dest_path}")
    dest_path.chmod(0o600)


def main() -> None:
    if len(sys.argv) != 3:
        sys.stderr.write("usage: bootstrap_env.py DEST EXAMPLE\n")
        sys.exit(1)
    dest = Path(sys.argv[1])
    example = Path(sys.argv[2])
    try:
        bootstrap(dest, example)
    except ValueError as exc:
        sys.stderr.write(f"bootstrap_env: {exc}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
