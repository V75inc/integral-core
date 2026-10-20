"""Measure authorized HTTP retrieval with a supplied, labelled corpus.

Gold JSON: [{"query": "...", "expected_entry_ids": ["..."],
             "answer_contains": ["..."]}]. No credentials in arguments/output.
Set INTEGRAL_BENCHMARK_TOKEN and pass --base-url/--scope/--gold.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument(
        "--request-interval",
        type=float,
        default=1.1,
        help="Pause outside latency measurement; respects a 60/minute endpoint budget",
    )
    parser.add_argument("--context-tokens", type=int, default=600)
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 50 or not 0 <= args.request_interval <= 60:
        parser.error("Use 1..50 repetitions and a 0..60 second interval")

    # Never forward a bearer credential through a redirect to another origin.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(NoRedirect())
    token = os.environ["INTEGRAL_BENCHMARK_TOKEN"]
    gold = json.loads(args.gold.read_text())
    rows = []
    for repetition in range(args.repetitions + 1):
        for mode in ("semantic", "hybrid", "graph"):
            for case in gold:
                body = json.dumps(
                    dict(
                        query=case["query"],
                        scope=args.scope,
                        mode=mode,
                        top_n=3,
                        context_tokens=args.context_tokens,
                    )
                ).encode()
                request = urllib.request.Request(
                    args.base_url.rstrip("/") + "/api/retrieve",
                    data=body,
                    headers={
                        "Authorization": "Bearer " + token,
                        "Content-Type": "application/json",
                    },
                )
                start = time.perf_counter()
                with opener.open(request, timeout=60) as response:
                    wire = response.read()
                elapsed = 1000 * (time.perf_counter() - start)
                data = json.loads(wire)
                results = data["results"]
                ids = [row["entry_id"] for row in results]
                expected = set(case["expected_entry_ids"])
                passages = [p for row in results for p in row.get("passages", [])]
                text = " ".join(p["text"] for p in passages)
                rows.append(
                    dict(
                        mode=mode,
                        query=case["query"],
                        repetition=repetition,
                        ms=elapsed,
                        top1=bool(ids and ids[0] in expected),
                        recall3=bool(set(ids) & expected),
                        answer_evidence=all(
                            s.lower() in text.lower()
                            for s in case.get("answer_contains", [])
                        ),
                        passage_tokens=sum(p["tokens"] for p in passages),
                        response_bytes=len(wire),
                    )
                )
                time.sleep(args.request_interval)
    summary = {}
    for mode in ("semantic", "hybrid", "graph"):
        warm = [r for r in rows if r["mode"] == mode and r["repetition"] > 0]
        times = sorted(r["ms"] for r in warm)
        summary[mode] = dict(
            requests=len(warm),
            p50_ms=statistics.median(times),
            p95_ms=times[int(0.95 * (len(times) - 1))],
            recall_at_1=statistics.mean(r["top1"] for r in warm),
            recall_at_3=statistics.mean(r["recall3"] for r in warm),
            answer_evidence=statistics.mean(r["answer_evidence"] for r in warm),
            mean_passage_tokens=statistics.mean(r["passage_tokens"] for r in warm),
        )
    args.output.write_text(json.dumps(dict(summary=summary, rows=rows), indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
