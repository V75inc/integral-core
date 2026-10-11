"""Reproduce local Core query adoption on a disposable SQLite database.

Run from backend: .venv/bin/python scripts/qualify_ontology_database.py
This measures warm local calls, not production capacity or an authorization SLA.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import tempfile
import time
from pathlib import Path

from jvspatial.core import GraphContext
from jvspatial.core.context import scoped_default_context
from jvspatial.db.sqlite import SQLiteDB

from app.models.nodes import User


async def main() -> None:
    """Measure first-match hydration and native collection batches."""
    with tempfile.TemporaryDirectory(prefix="integral-ontology-probe-") as folder:
        db = SQLiteDB(db_path=Path(folder) / "probe.db")
        ctx = GraphContext(db)
        try:
            # Disposable raw fixture records, never a live Integral graph.
            # It models legacy duplicate auth mappings, the actual lookup key
            # optimized in permissions.get_user_node, with independent IDs.
            records = [
                {
                    "id": f"n.User.probe{i:05d}",
                    "entity": "User",
                    "context": {"user_id": "auth-probe", "display_name": f"User {i}"},
                }
                for i in range(5000)
            ]
            await db.bulk_save("node", records)
            result = {}
            with scoped_default_context(ctx):
                for name, function in (
                    ("full_matches", User.find),
                    ("first_match", User.find_one),
                ):
                    samples = []
                    for _ in range(8):
                        await ctx.clear_cache()
                        started = time.perf_counter()
                        value = await function({"context.user_id": "auth-probe"})
                        elapsed = (time.perf_counter() - started) * 1000
                        if _ > 0:
                            samples.append(elapsed)
                    result[name] = {
                        "median_ms": round(statistics.median(samples), 3),
                        "hydrated_rows": (
                            len(value)
                            if isinstance(value, list)
                            else int(value is not None)
                        ),
                    }
                result["same_first_identity"] = (
                    await User.find({"context.user_id": "auth-probe"})
                )[0].id == (await User.find_one({"context.user_id": "auth-probe"})).id
            writes = [
                {"id": f"o.Probe.{i}", "entity": "Probe", "context": {"value": i}}
                for i in range(200)
            ]
            for name, batched in (
                ("serial_writes", False),
                ("native_batch_writes", True),
            ):
                started = time.perf_counter()
                if batched:
                    outcome = await db.bulk_save_detailed("batch_probe", writes)
                    saved = outcome.saved
                else:
                    for row in writes:
                        await db.save("serial_probe", row)
                    saved = len(writes)
                result[name] = {
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                    "saved": saved,
                }
            result["native_graph_paging"] = db.supports_native_graph_paging
            result["qualification"] = (
                "warm SQLite local diagnostics; no production claim"
            )
            print(json.dumps(result, indent=2))
        finally:
            await db.close()


if __name__ == "__main__":
    asyncio.run(main())
