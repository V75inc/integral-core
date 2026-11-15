"""CLI boundary: migration credentials are never taken from runtime fallback."""

import json
import os
from pathlib import Path
from urllib.parse import quote

from . import catalog
from .inspection import digest
from .lifecycle import PlanError, apply, plan
from .runtime import inspect


def connection_dsn(*, migration=False):
    """Select read-only runtime or explicit maintenance connection configuration."""
    if migration:
        value = os.getenv("INTEGRAL_MIGRATION_POSTGRES_DSN", "")
        if not value:
            raise PlanError(
                "INTEGRAL_MIGRATION_POSTGRES_DSN is required outside serving containers"
            )
        return value
    return os.getenv("JVSPATIAL_POSTGRES_DSN") or "postgresql://{}:{}@{}:{}/{}".format(
        quote(os.getenv("POSTGRES_USER", "integral"), safe=""),
        quote(os.getenv("POSTGRES_PASSWORD", "integral"), safe=""),
        os.getenv("POSTGRES_HOST", "localhost"),
        os.getenv("POSTGRES_PORT", "5433"),
        quote(os.getenv("POSTGRES_DB", "integral"), safe=""),
    )


def source_catalog(args, artifact=None):
    """Bind approved definitions to versioned, content-addressed source inputs."""
    import hashlib
    from dataclasses import replace

    from .proposals import approved_specs, normalize_requests

    include_search = artifact["include_search"] if artifact else not args.no_search
    schema = artifact["target"]["schema"] if artifact else args.schema
    bindings = (
        artifact.get("source_inputs", [])
        if artifact
        else [
            {"kind": kind, "path": str(Path(path).resolve())}
            for kind, paths in (
                ("app", args.app_manifest or []),
                ("profile", args.profile or []),
                ("approval", args.approved_app_catalog or []),
            )
            for path in paths
        ]
    )
    selected = (
        set(artifact.get("selected_requests", []))
        if artifact
        else set(args.include_request or [])
    )
    specs = catalog.build_catalog(schema=schema, include_search=include_search)
    found = set()
    approvals = []
    for binding in bindings:
        if binding["kind"] == "approval":
            raw = Path(binding["path"]).read_bytes()
            if len(raw) > 10 * 1024 * 1024:
                raise PlanError("Approval input exceeds size limit")
            identity = hashlib.sha256(raw).hexdigest()
            if binding.get("sha256") and binding["sha256"] != identity:
                raise PlanError("Qualified platform catalog changed")
            binding["sha256"] = identity
            approvals.extend(json.loads(raw))
    for binding in bindings:
        if binding["kind"] == "approval":
            continue
        data = Path(binding["path"]).read_bytes()
        if len(data) > 10 * 1024 * 1024:
            raise PlanError("Schema input exceeds size limit")
        identity = hashlib.sha256(data).hexdigest()
        if binding.get("sha256") and binding["sha256"] != identity:
            raise PlanError("Reviewed source input changed")
        binding["sha256"] = identity
        if binding["kind"] == "app":
            import yaml

            manifest = yaml.safe_load(data)
            app = manifest.get("app") or {}
            if app.get("storage_index_requests_version", 1) != 1:
                raise PlanError("Unsupported storage request capability")
            requests = normalize_requests(
                app.get("storage_index_requests"),
                queries=app.get("queries", []),
                tracks=app.get("tracks", []),
            )
            compiled = {
                "app": {"storage_index_requests": requests},
                "package": manifest.get("package", {}),
            }
            proposed = approved_specs(
                compiled, selected, schema=schema, approvals=approvals
            )
            found.update(s.logical_id for s in proposed)
            specs.extend(proposed)
        else:
            from app.services.retrieval.index_definition import EmbeddingProfile

            from .search import profile_ann_spec

            specs.append(
                profile_ann_spec(EmbeddingProfile(**json.loads(data)), schema=schema)
            )
    if selected - found:
        raise PlanError("Selected App index requests were not found")
    dedup = {}
    for spec in specs:
        prior = dedup.get(spec.fingerprint)
        if prior:
            spec = replace(
                spec,
                required=spec.required or prior.required,
                queries=tuple(sorted(set(spec.queries + prior.queries))),
            )
        dedup[spec.fingerprint] = spec
    return (
        sorted(dedup.values(), key=lambda s: s.fingerprint),
        bindings,
        sorted(selected),
    )


async def execute(args):
    """Execute a planning, provisioning or status command with separate credentials."""
    import asyncpg

    artifact = (
        json.loads(Path(args.plan).read_text()) if args.action == "apply" else None
    )
    specs, bindings, selected = source_catalog(args, artifact)
    dsn = connection_dsn(migration=args.action == "apply")
    conn = await asyncpg.connect(dsn)
    try:
        if args.action == "status":
            result = await inspect(
                conn, specs, schema=args.schema, enforce_runtime_role=True
            )
            print(json.dumps(result, sort_keys=True))
            return int(
                args.check
                and (
                    not result["ready"]
                    or bool(result["budget_violations"])
                    or bool(result["invalid"])
                )
            )
        if args.action == "plan":
            evidence = (
                json.loads(Path(args.retirement_evidence).read_text())
                if args.retirement_evidence
                else None
            )
            artifact = await plan(
                conn,
                specs,
                schema=args.schema,
                max_indexes=args.max_indexes,
                max_index_bytes=args.max_index_bytes,
                max_build_bytes=args.max_build_bytes,
                include_search=not args.no_search,
                removals=args.remove or (),
                runtime_role=args.runtime_role,
                repairs=args.repair or (),
                retirement_evidence=evidence,
            )
            artifact.update(source_inputs=bindings, selected_requests=selected)
            artifact["hash"] = digest(
                {k: v for k, v in artifact.items() if k != "hash"}
            )
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(artifact, stream, indent=2, sort_keys=True)
            print(
                json.dumps(
                    {
                        "plan_hash": artifact["hash"],
                        "operations": len(artifact["operations"]),
                    }
                )
            )
            return 0
        result = await apply(
            conn, artifact, approved_hash=args.approved_hash, expected_specs=specs
        )
        print(json.dumps(result))
        return 0
    finally:
        await conn.close()
