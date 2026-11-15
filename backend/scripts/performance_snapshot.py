"""Print an allowlisted runtime fingerprint; safe to run inside an API image.

No application import, environment dump, connection URI, token or data read.
"""

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

DISTRIBUTIONS = [
    "integral-core",
    "integral-sdk",
    "jvspatial",
    "pydantic-ai-harness",
    "pydantic-ai",
    "asyncpg",
    "opentelemetry-api",
    "opentelemetry-sdk",
    "opentelemetry-exporter-otlp-proto-http",
]
SETTINGS = [
    "JVSPATIAL_DB_TYPE",
    "JVSPATIAL_POSTGRES_MIN_POOL_SIZE",
    "JVSPATIAL_POSTGRES_MAX_POOL_SIZE",
    "JVSPATIAL_POSTGRES_POOLER_MODE",
    "INTEGRAL_POSTGRES_PLAN_CACHE_MODE",
    "JVSPATIAL_OBSERVABILITY_ENABLED",
    "JVSPATIAL_SLOW_QUERY_MS",
    "INTEGRAL_PERF_TRACE",
    "INTEGRAL_PERF_HEADER_ENABLED",
    "OTEL_TRACES_SAMPLER",
    "OTEL_TRACES_SAMPLER_ARG",
]


def snapshot(repositories=(), artifacts=()):
    versions = {}
    for name in DISTRIBUTIONS:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    result = {
        "python": sys.version.split()[0],
        "versions": versions,
        "paths": {
            name: (spec.origin if (spec := importlib.util.find_spec(name)) else None)
            for name in ["jvspatial", "app"]
        },
        "settings": {name: os.getenv(name) for name in SETTINGS},
        "exporter_configured": bool(os.getenv("INTEGRAL_OTLP_ENDPOINT")),
    }
    result["repositories"] = []
    for directory in repositories:
        root = Path(directory).resolve()

        def git(*args):
            return subprocess.check_output(["git", "-C", str(root), *args])

        result["repositories"].append(
            {
                "path": str(root),
                "commit": git("rev-parse", "HEAD").decode().strip(),
                "branch": git("branch", "--show-current").decode().strip(),
                "dirty": bool(git("status", "--porcelain")),
                "tracked_diff_sha256": hashlib.sha256(git("diff", "HEAD")).hexdigest(),
            }
        )
    # Supply reviewed build outputs explicitly; never crawl runtime directories.
    result["artifacts"] = [
        {
            "path": str(Path(path).resolve()),
            "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        }
        for path in artifacts
    ]
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", action="append", default=[])
    parser.add_argument("--artifact", action="append", default=[])
    args = parser.parse_args()
    print(json.dumps(snapshot(args.repository, args.artifact), indent=2))
