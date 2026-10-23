"""Compare repeated definition embedding with bounded vector reuse.

Synthetic metadata only; no LLM requests, credentials, graph reads or writes.
Run from backend: HF_HUB_OFFLINE=1 .venv/bin/python scripts/benchmark_capability_discovery.py
This measures the discovery ranking stage, not end-to-end agent latency.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def benchmark(count: int, runs: int) -> dict:
    from app.agentive.harness import capability_search as search
    from app.services.retrieval.embedding_model import DEFAULT_MODEL_NAME

    documents = [
        SimpleNamespace(
            name=f"fixture_read_{i}",
            description=f"Find authorized synthetic records of category {i}",
            parameters_json_schema={"properties": {"query": {"type": "string"}}},
        )
        for i in range(count)
    ]
    original = search._definition_vector
    await search._embed_text_for_capability_search("warm encoder")
    results = {}
    try:
        for name, function in [
            ("reembed_definitions", search._embed_text_for_capability_search),
            ("reuse_definitions", original),
        ]:
            search._DEFINITION_VECTORS.clear()
            search._definition_vector = function
            # Warm the definition corpus separately from timed repeats.
            await search.pydantic_tool_search_strategy(
                None, ["find records"], documents
            )
            elapsed = []
            for _ in range(runs):
                started = time.perf_counter()
                await search.pydantic_tool_search_strategy(
                    None, ["find records"], documents
                )
                elapsed.append((time.perf_counter() - started) * 1000)
            results[name] = {
                "samples_ms": elapsed,
                "median_ms": statistics.median(elapsed),
                "max_ms": max(elapsed),
            }
    finally:
        search._definition_vector = original
        search._DEFINITION_VECTORS.clear()
    return {
        "embedding_model": DEFAULT_MODEL_NAME,
        "definitions": count,
        "runs": runs,
        "scope": "warm local ranking only; no provider inference",
        **results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--definitions", type=int, default=40, choices=range(1, 257))
    parser.add_argument("--runs", type=int, default=5, choices=range(1, 21))
    args = parser.parse_args()
    print(json.dumps(asyncio.run(benchmark(args.definitions, args.runs)), indent=2))
