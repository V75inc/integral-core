#!/usr/bin/env python3
"""Run one qualification lane and retain a machine-readable local evidence record.

The runner intentionally records a failed command rather than hiding it behind
an aggregate shell target. It does not decide release status; the acceptance
ledger remains the authoritative record for a frozen candidate.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path


LANES = {
    "repository": ["make", "verify"],
    "core-only": ["make", "verify-core-only"],
    "contract": ["make", "verify-contract"],
    "artifacts": ["make", "verify-independent-artifacts"],
    "postgres": ["make", "test-postgres"],
}


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _git_revision(root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()


def _configuration_digest() -> str:
    names = [
        "INTEGRAL_TEST_DB",
        "JVSPATIAL_DB_TYPE",
        "JVSPATIAL_POSTGRES_DSN",
        "INTEGRAL_CORE_ONLY",
        "TESTING",
    ]
    # Values may contain credentials. Keep only environment-variable names and
    # presence flags in the evidence digest.
    shape = {name: bool(os.environ.get(name)) for name in names}
    encoded = json.dumps(shape, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("lane", choices=sorted(LANES))
    parser.add_argument("--output-dir", default=".qualification-evidence")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    output_dir = (root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = _utc_now()
    command = LANES[args.lane]
    completed = subprocess.run(command, cwd=root, text=True, capture_output=True)
    finished_at = _utc_now()
    stamp = started_at.replace(":", "-").replace("+00:00", "Z")
    log_path = output_dir / f"{stamp}-{args.lane}.log"
    log_path.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    record = {
        "schema_version": 1,
        "lane": args.lane,
        "command": command,
        "exit_code": completed.returncode,
        "result": "pass" if completed.returncode == 0 else "fail",
        "started_at_utc": started_at,
        "finished_at_utc": finished_at,
        "git_revision": _git_revision(root),
        "runtime_versions": {"python": sys.version, "platform": platform.platform()},
        "non_secret_configuration_digest": _configuration_digest(),
        "fixture_manifest": "docs/product/evidence/phase-0-qualification-manifest.yaml",
        "log": str(log_path.relative_to(root)),
    }
    record_path = output_dir / f"{stamp}-{args.lane}.json"
    record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
