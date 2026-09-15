"""Recompute ``*_fold`` columns that predate accent-insensitive folding.

Why this exists
---------------
jvspatial used to ASCII-fold every persisted string
(``JVSPATIAL_TEXT_NORMALIZATION_ENABLED``, default true). A workspace named
"Café" was therefore stored as "Cafe", and ``compute_fold`` -- a plain
casefold at the time -- produced "cafe".

Turning normalization off fixed display text but split the fold columns: rows
written afterwards stored "Café" and folded to "café", which no longer matched
the "cafe" on every older row. Uniqueness keys off these columns, so a
duplicate that looks identical in the UI could be created.

``compute_fold`` now strips accents itself, so new writes are consistent with
the old rows again. This script repairs the rows written in between.

Idempotent: it only writes where the stored fold differs from the recomputed
one, so a second run is a no-op.

Usage::

    python -m scripts.backfill_fold_columns --dry
    python -m scripts.backfill_fold_columns
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# (Node class name, source attribute, fold attribute)
TARGETS = [
    ("Workspace", "name", "name_fold"),
    ("App", "name", "name_fold"),
    ("Track", "title", "title_fold"),
    ("Tag", "name", "name_fold"),
    ("EntryType", "name", "name_fold"),
    ("ContentProfile", "name", "name_fold"),
    ("View", "name", "name_fold"),
]


async def main() -> int:
    """Recompute drifted fold columns; return a process exit code."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="report without writing")
    args = parser.parse_args()

    from app import models  # noqa: F401  (registers node classes)
    from app.api.validators_common import compute_fold
    from app.models import nodes as node_module

    total_checked = 0
    total_fixed = 0
    for cls_name, src_attr, fold_attr in TARGETS:
        cls = getattr(node_module, cls_name, None)
        if cls is None:
            print(f"  {cls_name}: class not found, skipped")
            continue
        rows = await cls.find({})
        drifted = []
        for row in rows:
            source = getattr(row, src_attr, "") or ""
            stored = getattr(row, fold_attr, "") or ""
            expected = compute_fold(source)
            # Only repair folds that drifted, never populate one that was
            # never set. A dry run over a real database showed 900 of 1036
            # rows with an EMPTY fold column -- node types the app simply
            # does not denormalize. Filling those in would switch on
            # uniqueness matching where the app never had any, which is a
            # far bigger behavioural change than the accent drift this
            # script exists to undo.
            if stored and stored != expected:
                drifted.append((row, stored, expected))
        total_checked += len(rows)
        for row, _stored, expected in drifted:
            if not args.dry:
                setattr(row, fold_attr, expected)
                await row.save()
        total_fixed += len(drifted)
        verb = "would fix" if args.dry else "fixed"
        print(f"  {cls_name:16} checked={len(rows):5} {verb}={len(drifted)}")
        for row, stored, expected in drifted[:5]:
            print(f"      {row.id}: {stored!r} -> {expected!r}")

    print(
        f"\n{'DRY RUN — ' if args.dry else ''}checked {total_checked}, "
        f"{'would fix' if args.dry else 'fixed'} {total_fixed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
