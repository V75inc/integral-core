"""Independent 50K/100K PostgreSQL correctness and performance gates.

The optional disabled-pushdown comparison is isolated from native qualification.
Passing cells collect every sample; failing cells stop only once the existing
nearest-rank p95 bound is mathematically impossible. Partial samples never pass.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.postgres,
    pytest.mark.slow,
    pytest.mark.skipif(
        (os.getenv("INTEGRAL_TEST_DB") or "json").lower()
        not in ("postgres", "postgresql"),
        reason="50k scale evidence requires isolated PostgreSQL test DB",
    ),
    pytest.mark.skipif(
        not os.getenv("INTEGRAL_RUN_SLOW_TESTS"), reason="50k scale evidence is opt-in"
    ),
]


def _runtime_source_fingerprint():
    import jvspatial

    roots = [
        Path(__file__).resolve().parents[1] / "app",
        Path(jvspatial.__file__).resolve().parent,
    ]
    digest = hashlib.sha256()
    for root in roots:
        for path in sorted(
            path
            for path in root.rglob("*")
            if path.suffix in {".py", ".yaml", ".yml", ".json"}
        ):
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    backend = Path(__file__).resolve().parents[1]
    for path in (
        Path(__file__).resolve(),
        backend / "pyproject.toml",
        backend / "uv.lock",
    ):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


@pytest.fixture(params=[50_000, 100_000], ids=["50k", "100k"])
async def scale_records(request):
    from jvspatial.api.auth.models import UserCreate
    from jvspatial.db import get_prime_database

    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.apps import create_app
    from app.api.auth import _get_auth_service
    from app.api.tracks import create_track
    from app.models.nodes import User
    from app.services import permissions
    from app.services.app_graph import catalog_user
    from app.services.personal_workspace import ensure_personal_workspace

    runtime_source = _runtime_source_fingerprint()
    size = request.param
    print(
        f"Reference runtime: {sys.version.split()[0]}; source SHA256: {runtime_source}",
        flush=True,
    )
    run_id = uuid.uuid4().hex[:12]
    auth = await _get_auth_service().register_user(
        UserCreate(email=f"scale-{run_id}@example.com", password="scale-test-pass")
    )
    user = await User.create(user_id=auth.id, display_name="Scale fixture")
    await catalog_user(user)
    workspace = await ensure_personal_workspace(user)
    app_result = await invoke_route_in_process(
        create_app,
        principal_id=auth.id,
        scope=workspace.id,
        name=f"Scale fixture {run_id}",
    )
    app_id = (app_result.get("app") or app_result.get("space") or {}).get("id")
    track_result = await invoke_route_in_process(
        create_track,
        principal_id=auth.id,
        scope=workspace.id,
        title=f"Scale track {run_id}",
        visibility="private",
        app_id=app_id,
    )
    track_id = track_result["track"]["id"]

    database = get_prime_database()
    inner = database
    for _ in range(4):
        nested = getattr(inner, "inner", None) or getattr(inner, "_db", None)
        if nested is None:
            break
        inner = nested
    if not hasattr(inner, "bulk_save") or not hasattr(inner, "_acquire_conn"):
        pytest.skip("test database is not the PostgreSQL backend")

    track = await __import__("app.models.nodes", fromlist=["Track"]).Track.get(track_id)
    context = await track.get_context()
    from app.models.nodes import Entry

    # Bulk seeding bypasses Node.save; reproduce the production boot indexes.
    await context.ensure_indexes(Entry)
    node_collection = context._get_collection_name("n")
    edge_collection = context._get_collection_name("e")
    schema = inner.schema_name
    ids = [f"n.Entry.scale-{run_id}-{i:06d}" for i in range(size)]
    match_id = ids[-1]
    timestamp = "2026-01-01T00:00:00+00:00"
    node_rows = [
        {
            "id": entry_id,
            "entity": "Entry",
            "context": {
                "title": "Selective match" if entry_id == match_id else "Background",
                "author_id": auth.id,
                "track_id": track_id,
                "type_id": "",
                "tags": [],
                "custom_fields": {},
                "status": "active",
                "body": "",
                "visibility": "inherit",
                "created_at": timestamp,
                "updated_at": timestamp,
            },
        }
        for entry_id in ids
    ]
    edge_rows = [
        {
            "id": f"e.CONTAINS.scale-{run_id}-{i:05d}",
            "entity": "CONTAINS",
            "source": track_id,
            "target": entry_id,
            "context": {"added_at": timestamp, "position": None},
            "bidirectional": False,
        }
        for i, entry_id in enumerate(ids)
    ]

    try:
        assert await database.bulk_save(node_collection, node_rows) == len(node_rows)
        assert await database.bulk_save(edge_collection, edge_rows) == len(edge_rows)

        assert (
            await database.count(
                node_collection, {"entity": "Entry", "context.track_id": track_id}
            )
            == size
        )
        assert (
            await database.count(
                edge_collection, {"entity": "CONTAINS", "source": track_id}
            )
            == size
        )

        # Warm qualification includes normal planner statistics after bulk seed.
        async with inner._acquire_conn() as connection:
            await connection.execute(f"ANALYZE {schema}.{node_collection}")
            await connection.execute(f"ANALYZE {schema}.{edge_collection}")
            print(
                f"PostgreSQL plan_cache_mode={await connection.fetchval('SHOW plan_cache_mode')}; pooler_mode={inner.pooler_mode}",
                flush=True,
            )
        del node_rows, edge_rows
        yield {
            "actor": auth.id,
            "workspace": workspace.id,
            "track": track_id,
            "ids": set(ids),
            "match": match_id,
            "size": size,
            "profile": user.id,
            "app": app_id,
        }
    finally:
        async with inner._acquire_conn() as connection:
            await connection.execute(
                f"DELETE FROM {schema}.{edge_collection} "
                "WHERE data->>'source' = $1 AND data->>'entity' = 'CONTAINS' "
                "AND data->>'target' = ANY($2::text[])",
                track_id,
                ids,
            )
            await connection.execute(
                f"DELETE FROM {schema}.{node_collection} WHERE id = ANY($1::text[])",
                ids,
            )

        assert (
            _runtime_source_fingerprint() == runtime_source
        ), "Runtime source changed during qualification; rerun against a stable candidate"


@pytest.mark.parametrize(
    "surface,budget_ms,samples",
    [
        ("selective", 250, 20),
        ("page", 500, 20),
        ("governed", 500, 20),
        ("keyword", 2000, 5),
        ("count", 500, 20),
        ("digest", 500, 20),
    ],
)
async def test_scale_surface_accuracy_and_budget(
    scale_records, surface, budget_ms, samples
):
    from app.services.agent_insights import (
        activity_digest,
        count_entries_grouped,
        query_entries,
    )

    f = scale_records
    # Preserve the original 50K gates. Freeze a linear-work ceiling for the
    # new 100K diagnostic before taking samples; neither is a hosted SLO.
    budget_ms *= f["size"] // 50_000

    async def operation():
        if surface == "governed":
            from app.schemas.governed_query import QuerySpec
            from app.services.governed_query.engine import execute_query

            result = await execute_query(
                user_id=f["actor"],
                workspace_id=f["workspace"],
                spec=QuerySpec(mode="core_open", resource="entry", limit=20),
            )
            assert result.total_estimate == f["size"]
            assert [row["id"] for row in result.rows] == sorted(f["ids"])[:20]
            assert all(
                ref.url_path and ref.workspace_id == f["workspace"]
                for ref in result.object_refs
            )
        elif surface == "count":
            result = await count_entries_grouped(
                user_id=f["actor"], group_by="track", workspace_id=f["workspace"]
            )
            assert result["total_matched"] == f["size"]
            assert (
                len(result["groups"]) == 1 and result["groups"][0]["count"] == f["size"]
            )
        elif surface == "digest":
            result = await activity_digest(
                user_id=f["actor"],
                workspace_id=f["workspace"],
                scope="track",
                scope_id=f["track"],
            )
            assert result["total_entries"] == f["size"]
        else:
            kwargs = {"user_id": f["actor"], "track_id": f["track"], "limit": 20}
            if surface == "selective":
                kwargs["filters"] = [
                    {"field": "title", "op": "eq", "value": "Selective match"}
                ]
            elif surface == "keyword":
                kwargs["query"] = "Selective match"
            result = await query_entries(**kwargs)
            if surface in {"keyword", "selective"}:
                assert result["total"] == 1
                assert [entry["id"] for entry in result["entries"]] == [f["match"]]
            else:
                assert result["total"] == f["size"]
                selected = [entry["id"] for entry in result["entries"]]
                assert len(selected) == len(set(selected)) == 20
                assert set(selected).issubset(f["ids"])
        return result

    # Every timed invocation validates records; one warm-up is also validated.
    await asyncio.wait_for(operation(), timeout=30)
    values = []
    allowed_exceedances = samples - math.ceil(0.95 * samples)
    for index in range(samples):
        started = time.perf_counter()
        await asyncio.wait_for(operation(), timeout=30)
        elapsed = (time.perf_counter() - started) * 1000
        values.append(elapsed)
        print(
            f"{surface} sample {index + 1}/{samples}: {elapsed:.1f}ms; exact records verified",
            flush=True,
        )
        assert sum(value > budget_ms for value in values) <= allowed_exceedances, (
            f"{surface}: frozen {budget_ms}ms p95 budget cannot pass after {len(values)} samples; "
            f"partial observations={values!r}; no passing percentile claimed"
        )
    p95 = sorted(values)[math.ceil(0.95 * samples) - 1]
    print(
        f"{f['size']} {surface} empirical p95: {p95:.1f}ms over {samples} samples",
        flush=True,
    )
    assert p95 <= budget_ms


async def test_scale_mixed_acl_surfaces_and_revocation(scale_records, client):
    """Large populations retain exclusions, direct grants and live revocation.

    This is a correctness matrix; timed owner budgets above are independent.
    Every boundary verifies actual identities/counts, never only HTTP success.
    """
    from jvspatial.api.auth.models import UserCreate, UserLogin

    from app.agentive.harness.broker_tools import _resource_links_for_model
    from app.agentive.mcp.server import _call_tool_impl
    from app.api.auth import _get_auth_service
    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.models.edges import COLLABORATES_ON, EXCLUDED_FROM, IS_MEMBER_OF
    from app.models.nodes import Entry, Track, User, Workspace
    from app.schemas.governed_query import QuerySpec
    from app.services.agent_insights import (
        activity_digest,
        count_entries_grouped,
        query_entries,
    )
    from app.services.app_graph import catalog_user
    from app.services.governed_query import execute_query
    from app.services.hooks.registry import ToolContext
    from tests.test_resource_location import graph

    f = scale_records
    auth_service = _get_auth_service()
    auth = await auth_service.register_user(
        UserCreate(
            email=f"reader-{uuid.uuid4().hex}@example.com", password="scale-test-pass"
        )
    )
    reader = await User.create(user_id=auth.id, display_name="Scale reader")
    await catalog_user(reader)
    workspace = await Workspace.get(f["workspace"])
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    track = await Track.get(f["track"])
    track_grant = await reader.connect(track, edge=COLLABORATES_ON, role="viewer")
    ids = sorted(f["ids"])
    denied = {ids[1], f["match"]}
    for identity in (ids[0], *denied):
        entry = await Entry.get(identity)
        await reader.connect(entry, edge=EXCLUDED_FROM)
        if identity == ids[0]:
            # A complete direct grant overrides inherited exclusions.
            await reader.connect(entry, edge=COLLABORATES_ON, role="viewer")
    _, foreign_workspace, _, _, foreign_entry = await graph()
    forbidden = denied | {foreign_entry.id}
    expected = f["size"] - len(denied)

    async def check_native():
        with isolated_permissions_cache():
            page = await query_entries(
                user_id=auth.id,
                workspace_id=f["workspace"],
                track_id=track.id,
                limit=20,
            )
            assert page["total"] == expected
            actual = {item["id"] for item in page["entries"]}
            assert len(actual) == 20 and not actual & forbidden
            count = await count_entries_grouped(
                user_id=auth.id, workspace_id=f["workspace"], group_by="track"
            )
            assert count["total_matched"] == expected
            hidden = await query_entries(
                user_id=auth.id, track_id=track.id, query="Selective match", limit=20
            )
            assert hidden["entries"] == [] and hidden["total"] == 0
        return page

    native = await asyncio.wait_for(check_native(), 60)
    resident = _resource_links_for_model(native)
    assert {item["id"] for item in resident["entries"]} == {
        item["id"] for item in native["entries"]
    }
    assert all(
        item["url"].startswith(f"/tracks/{track.id}?entry=")
        for item in resident["entries"]
    )

    with isolated_permissions_cache():
        governed = await asyncio.wait_for(
            execute_query(
                user_id=auth.id,
                workspace_id=f["workspace"],
                spec=QuerySpec(mode="core_open", resource="entry", limit=20),
            ),
            60,
        )
        assert governed.total_estimate == expected
        assert not {row["id"] for row in governed.rows} & forbidden
    ctx = ToolContext(
        user_id=auth.id, workspace_id=f["workspace"], scope=f"ws:{f['workspace']}"
    )
    sdk = await asyncio.wait_for(
        ctx.query({"mode": "core_open", "resource": "entry", "limit": 20}), 60
    )
    assert sdk["total_estimate"] == expected
    assert not {row["id"] for row in sdk["rows"]} & forbidden

    mcp = await asyncio.wait_for(
        _call_tool_impl(
            "integral_query_entries",
            {"track_id": track.id, "limit": 20},
            principal_id=auth.id,
            scope=f["workspace"],
        ),
        60,
    )
    assert isinstance(mcp, dict) and mcp["total"] == expected
    assert not {row["id"] for row in mcp["entries"]} & forbidden

    login = await auth_service.login_user(
        UserLogin(email=auth.email, password="scale-test-pass")
    )
    # The public client traverses the real authentication and workspace stack.
    response = await client.get(
        "/api/entries",
        params={"track_id": track.id, "limit": 20},
        headers={
            "Authorization": f"Bearer {login.access_token}",
            "X-Integral-Scope": f"ws:{f['workspace']}",
        },
    )
    assert response.status_code == 200, response.text
    http = response.json()
    assert http["total"] == expected
    assert not {row["id"] for row in http["entries"]} & forbidden

    # A different selected workspace cannot widen authority via a supplied ID.
    foreign = await _call_tool_impl(
        "integral_query_entries",
        {"track_id": track.id},
        principal_id=auth.id,
        scope=foreign_workspace.id,
    )
    assert not isinstance(foreign, dict) or not foreign.get("entries")
    await track_grant.delete()
    with isolated_permissions_cache():
        remaining = await query_entries(
            user_id=auth.id, workspace_id=f["workspace"], track_id=track.id, limit=20
        )
        assert remaining["total"] == 1
        assert [item["id"] for item in remaining["entries"]] == [ids[0]]
        broad = await query_entries(
            user_id=auth.id, workspace_id=f["workspace"], limit=20
        )
        assert broad["total"] == 1
        assert [item["id"] for item in broad["entries"]] == [ids[0]]
        count = await count_entries_grouped(
            user_id=auth.id, workspace_id=f["workspace"], group_by="track"
        )
        assert count["total_matched"] == 1
        assert count["groups"][0]["label"] == "Shared records"
        digest = await activity_digest(user_id=auth.id, workspace_id=f["workspace"])
        assert digest["total_entries"] == 1
        assert digest["track_summaries"][0]["title"] == "Shared records"
        assert digest["track_summaries"][0]["action_url"] is None
    print(
        f"{f['size']} mixed ACL: service, governed, SDK, resident projection, MCP and authenticated HTTP verified; revocation leaves only the direct grant",
        flush=True,
    )


@pytest.mark.skipif(
    os.getenv("INTEGRAL_EXPLAIN_SCALE") != "1", reason="explicit SQL plan diagnostic"
)
async def test_scale_repeated_count_plan_diagnostic(scale_records, monkeypatch):
    """Retain native adapter plans when repeated large counts change latency."""
    from jvspatial.db import get_prime_database

    from app.services.agent_insights import count_entries_grouped

    f = scale_records
    inner = get_prime_database()
    for _ in range(4):
        nested = getattr(inner, "inner", None) or getattr(inner, "_db", None)
        if nested is None:
            break
        inner = nested
    acquire = inner._acquire_conn
    statements = []
    selected_mode = "auto"

    @asynccontextmanager
    async def observed():
        async with acquire() as connection:
            await connection.execute(f"SET plan_cache_mode = {selected_mode}")
            with connection.query_logger(statements.append):
                yield connection

    monkeypatch.setattr(inner, "_acquire_conn", observed)
    samples = {}
    for selected_mode in ("auto", "force_custom_plan", "force_generic_plan"):
        samples[selected_mode] = []
        for _ in range(10):
            start = time.perf_counter()
            result = await count_entries_grouped(
                user_id=f["actor"], workspace_id=f["workspace"], group_by="track"
            )
            assert result["total_matched"] == f["size"]
            samples[selected_mode].append((time.perf_counter() - start) * 1000)
    await asyncio.sleep(0)
    native = next(
        record
        for record in statements
        if "WITH candidates AS MATERIALIZED" in record.query
    )
    plans = {}
    async with acquire() as connection:
        for mode in ("force_custom_plan", "force_generic_plan"):
            async with connection.transaction():
                await connection.execute(f"SET LOCAL plan_cache_mode = {mode}")
                raw = await connection.fetchval(
                    "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + native.query,
                    *native.args,
                )
                plans[mode] = json.loads(raw) if isinstance(raw, str) else raw
    path = Path(
        os.getenv("INTEGRAL_SCALE_PLAN_OUTPUT", "/tmp/integral-scale-plans.json")
    )
    path.write_text(
        json.dumps({"size": f["size"], "samples_ms": samples, "plans": plans}, indent=2)
        + "\n"
    )
    print(f"{f['size']} repeated count samples_ms={samples}; plans={path}", flush=True)


@pytest.mark.skipif(
    os.getenv("INTEGRAL_COMPARE_UNOPTIMIZED") != "1",
    reason="disabled-pushdown comparison is explicitly opt-in",
)
async def test_50k_unoptimized_comparator_is_isolated(scale_records, monkeypatch):
    from app.services import permissions
    from app.services.agent_insights import query_entries

    f = scale_records
    original = permissions.get_user_accessible_entries

    async def without_candidate_pushdown(user_id, track_id, **kwargs):
        kwargs.pop("candidate_query", None)
        return await original(user_id, track_id, **kwargs)

    with monkeypatch.context() as comparison:
        comparison.setattr(
            permissions, "get_user_accessible_entries", without_candidate_pushdown
        )
        result = await asyncio.wait_for(
            query_entries(
                user_id=f["actor"],
                track_id=f["track"],
                filters=[{"field": "title", "op": "eq", "value": "Selective match"}],
                limit=20,
            ),
            timeout=30,
        )
        assert result["total"] == 1 and result["entries"][0]["id"] == f["match"]
    assert permissions.get_user_accessible_entries is original
