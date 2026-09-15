"""Capture Mongo explain plans for dominant query shapes on integral_db.

Run before + after the Phase 2-4 index work to confirm IXSCAN coverage. Persists
output as JSON for diffing.

Usage:
    python backend/scripts/perf_explain.py --out .planning/perf-baseline-pre.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient

DB_NAME = os.environ.get("JVSPATIAL_MONGODB_DB_NAME", "integral_db")
MONGO_URI = os.environ.get("JVSPATIAL_MONGODB_URI", "mongodb://localhost:27017")


# Each probe is a (label, collection, filter, sort, hint?) tuple. Keep filters
# representative of the hottest list-endpoint shapes.
PROBES: list[dict[str, Any]] = [
    {
        "label": "edge:outgoing-by-source-entity",
        "collection": "edge",
        "filter": {"entity": "CONTAINS", "source": "n.Track.example"},
        "sort": None,
    },
    {
        "label": "edge:incoming-by-target-entity",
        "collection": "edge",
        "filter": {"entity": "CONTAINS", "target": "n.Track.example"},
        "sort": None,
    },
    {
        "label": "edge:entity-only",
        "collection": "edge",
        "filter": {"entity": "COLLABORATES_ON"},
        "sort": None,
    },
    {
        "label": "node:entity-discriminator",
        "collection": "node",
        "filter": {"entity": "Entry"},
        "sort": None,
    },
    {
        "label": "node:entry-by-track",
        "collection": "node",
        "filter": {"entity": "Entry", "context.track_id": "n.Track.example"},
        "sort": [("context.created_at", -1)],
    },
    {
        "label": "node:entry-by-workspace-sort-created",
        "collection": "node",
        "filter": {"entity": "Entry", "context.workspace_id": "n.Workspace.example"},
        "sort": [("context.created_at", -1)],
    },
    {
        "label": "node:entry-by-author",
        "collection": "node",
        "filter": {"entity": "Entry", "context.author_id": "n.User.example"},
        "sort": None,
    },
    {
        "label": "node:entrytype-by-track",
        "collection": "node",
        "filter": {"entity": "EntryType", "context.track_id": "n.Track.example"},
        "sort": None,
    },
    {
        "label": "node:workspace-by-kind",
        "collection": "node",
        "filter": {"entity": "Workspace", "context.kind": "personal"},
        "sort": None,
    },
    {
        "label": "node:user-by-user-id",
        "collection": "node",
        "filter": {"entity": "User", "context.user_id": "o.User.example"},
        "sort": None,
    },
    {
        "label": "node:track-by-workspace",
        "collection": "node",
        "filter": {"entity": "Track", "context.workspace_id": "n.Workspace.example"},
        "sort": [("context.updated_at", -1)],
    },
    {
        "label": "node:app-by-workspace",
        "collection": "node",
        "filter": {
            "entity": "WorkspaceApp",
            "context.workspace_id": "n.Workspace.example",
        },
        "sort": [("context.updated_at", -1)],
    },
]


def _winning_plan_summary(explain: dict) -> dict[str, Any]:
    """Extract IXSCAN vs COLLSCAN + index name from explain output."""
    qp = explain.get("queryPlanner", {})
    wp = qp.get("winningPlan", {})
    stages: list[str] = []
    indexes: list[str] = []

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        stage = node.get("stage")
        if stage:
            stages.append(stage)
        idx = node.get("indexName")
        if idx:
            indexes.append(idx)
        for child_key in ("inputStage", "inputStages"):
            child = node.get(child_key)
            if isinstance(child, list):
                for c in child:
                    walk(c)
            elif isinstance(child, dict):
                walk(child)

    walk(wp)
    ex = explain.get("executionStats", {})
    return {
        "stages": stages,
        "indexes_used": indexes,
        "docs_examined": ex.get("totalDocsExamined"),
        "keys_examined": ex.get("totalKeysExamined"),
        "n_returned": ex.get("nReturned"),
        "exec_ms": ex.get("executionTimeMillis"),
        "scan_kind": (
            "IXSCAN"
            if "IXSCAN" in stages
            else ("COLLSCAN" if "COLLSCAN" in stages else "OTHER")
        ),
    }


async def run_probes(out_path: Path) -> None:
    client = AsyncIOMotorClient(MONGO_URI)
    try:
        db = client[DB_NAME]
        results: list[dict[str, Any]] = []
        for probe in PROBES:
            collection = db[probe["collection"]]
            cmd: dict[str, Any] = {
                "find": probe["collection"],
                "filter": probe["filter"],
                "limit": 50,
            }
            if probe["sort"]:
                cmd["sort"] = dict(probe["sort"])
            explain = await db.command({"explain": cmd, "verbosity": "executionStats"})
            summary = _winning_plan_summary(explain)
            results.append(
                {"probe": probe["label"], "filter": probe["filter"], **summary}
            )
            print(
                f"{probe['label']:50s}  {summary['scan_kind']:8s}  "
                f"docs={summary['docs_examined']}  keys={summary['keys_examined']}  "
                f"idx={summary['indexes_used']}"
            )

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(results, indent=2, default=str))
        print(f"\nWrote {len(results)} probe results → {out_path}")
    finally:
        client.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out",
        default=".planning/perf-baseline-pre.json",
        help="Output path for JSON results",
    )
    args = ap.parse_args()
    asyncio.run(run_probes(Path(args.out)))


if __name__ == "__main__":
    main()
