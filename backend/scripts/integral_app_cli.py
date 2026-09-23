#!/usr/bin/env python3
"""Minimal public-facing App inspect helper (WP-10 slice).

Usage:
  python -m scripts.integral_app_cli validate examples/asset-register
  python -m scripts.integral_app_cli inspect examples/asset-register
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_yaml(path: Path) -> dict:
    import yaml

    return yaml.safe_load(path.read_text()) or {}


def cmd_validate(package_dir: Path) -> int:
    profile = package_dir / "operational-model.yaml"
    if not profile.exists():
        print(f"missing operational-model.yaml under {package_dir}", file=sys.stderr)
        return 1
    raw = _load_yaml(profile)
    app = raw.get("app") or {}
    errors = []
    if not (raw.get("package") or {}).get("slug"):
        errors.append("package.slug required")
    queries = app.get("queries") or []
    for q in queries:
        if not q.get("key"):
            errors.append("query missing key")
        if not (q.get("tool") or q.get("handler_ref")):
            errors.append(f"query {q.get('key')!r} needs tool or handler_ref")
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "queries": len(queries),
                "operations": len(app.get("operations") or []),
            }
        )
    )
    return 0


def cmd_inspect(package_dir: Path) -> int:
    profile = package_dir / "operational-model.yaml"
    raw = _load_yaml(profile)
    app = raw.get("app") or {}
    pkg = raw.get("package") or {}
    print(
        json.dumps(
            {
                "slug": pkg.get("slug"),
                "version": pkg.get("version"),
                "queries": [q.get("key") for q in (app.get("queries") or [])],
                "operations": [o.get("key") for o in (app.get("operations") or [])],
                "protected_state": app.get("protected_state") or {},
                "extension_views": [
                    v.get("key") for v in (app.get("extension_views") or [])
                ],
            },
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="integral-app")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("validate", "inspect"):
        p = sub.add_parser(name)
        p.add_argument("package_dir", type=Path)
    args = parser.parse_args(argv)
    if args.cmd == "validate":
        return cmd_validate(args.package_dir)
    if args.cmd == "inspect":
        return cmd_inspect(args.package_dir)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
