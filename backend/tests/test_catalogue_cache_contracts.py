"""Registry removal and same-key contract edits must invalidate disclosure."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.api.errors import BadRequestError
from app.schemas.capabilities import CapabilityCatalogueSnapshot, CapabilityDescriptor
from app.services.capability_catalogue import compile as catalogue


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["remove", "schema", "unchanged"])
async def test_cached_catalogue_tracks_registry_contract_changes(monkeypatch, change):
    from app.services.app_operations import registry as operations
    from app.services.app_queries import registry

    workspace = "catalogue-regression-workspace"
    definitions = {"app": {"read": {"input_schema": {"type": "object"}}}}
    monkeypatch.setattr(registry, "all_workspace_query_apps", lambda _ws: definitions)
    monkeypatch.setitem(operations._OPERATIONS, workspace, {})
    snapshot = CapabilityCatalogueSnapshot(
        workspace_id=workspace,
        generation_id="old",
        compiled_at="2026-10-31T00:00:00Z",
        capabilities=[
            CapabilityDescriptor(
                namespace="example",
                key="read",
                kind="query",
                name="Read",
                effects="read",
                app_id="app",
            )
        ],
    )
    monkeypatch.setitem(catalogue._ACTIVE_SNAPSHOT, workspace, snapshot)
    monkeypatch.setattr(
        catalogue, "_app_definition_fingerprint", AsyncMock(return_value="apps")
    )
    monkeypatch.setitem(catalogue._ACTIVE_APP_DEFINITIONS, workspace, "apps")
    monkeypatch.setitem(
        catalogue._ACTIVE_REGISTRY_DEFINITIONS,
        workspace,
        catalogue._registry_definition_fingerprint(workspace),
    )
    replacement = snapshot.model_copy(update={"generation_id": "new"})
    compile_snapshot = AsyncMock(return_value=replacement)
    monkeypatch.setattr(catalogue, "compile_workspace_catalogue", compile_snapshot)
    if change == "remove":
        definitions.clear()
    elif change == "schema":
        definitions["app"]["read"]["input_schema"]["required"] = ["cursor"]
    result = await catalogue.get_or_compile_catalogue(workspace)
    if change == "unchanged":
        assert result is snapshot
        compile_snapshot.assert_not_awaited()
    else:
        assert result is replacement
        compile_snapshot.assert_awaited_once_with(workspace, activate=True)


@pytest.fixture
async def registered_catalogue(monkeypatch):
    from app.services.app_queries import registry
    from tests.test_resource_location import graph

    owner, workspace, app, _, _ = await graph()
    monkeypatch.setitem(registry._QUERIES, workspace.id, {})
    schema = {"type": "object", "properties": {"query": {"type": "string"}}}
    registry.register_app_queries(
        workspace.id,
        app.id,
        "test-extension",
        [
            {
                "key": "lookup",
                "input_schema": schema,
                "output_schema": {"type": "object"},
            }
        ],
    )
    for cache in (
        "_ACTIVE_GENERATION",
        "_ACTIVE_SNAPSHOT",
        "_ACTIVE_REGISTRY_DEFINITIONS",
        "_ACTIVE_APP_DEFINITIONS",
    ):
        monkeypatch.setattr(catalogue, cache, {})
    return owner, workspace, app, schema


@pytest.mark.asyncio
async def test_generation_is_stable_across_restart_and_concurrent_workers(
    registered_catalogue, monkeypatch
):
    import asyncio

    from jvspatial.core.context import get_default_context

    from app.models.catalogue_generation import CatalogueGeneration

    _, workspace, _, _ = registered_catalogue
    first, concurrent = await asyncio.gather(
        catalogue.compile_workspace_catalogue(workspace.id),
        catalogue.compile_workspace_catalogue(workspace.id),
    )
    assert first.generation_id == concurrent.generation_id
    for cache in (
        "_ACTIVE_GENERATION",
        "_ACTIVE_SNAPSHOT",
        "_ACTIVE_REGISTRY_DEFINITIONS",
        "_ACTIVE_APP_DEFINITIONS",
    ):
        monkeypatch.setattr(catalogue, cache, {})
    restarted = await catalogue.get_or_compile_catalogue(workspace.id)
    assert restarted.generation_id == first.generation_id
    assert (
        await get_default_context().database.count(
            "object",
            {
                "entity": CatalogueGeneration._entity_name(),
                "context.workspace_id": workspace.id,
            },
        )
        == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,availability",
    [
        ("paused", "paused"),
        ("installing", "unavailable"),
        ("awaiting_settings", "unavailable"),
        ("uninstalled", "unavailable"),
    ],
)
async def test_persisted_lifecycle_change_invalidates_other_worker_cache(
    registered_catalogue, state, availability
):
    from jvspatial.core.context import get_default_context

    _, workspace, app, _ = registered_catalogue
    first = await catalogue.get_or_compile_catalogue(workspace.id)
    # Mutate primary storage without this worker's lifecycle invalidation hook.
    database = get_default_context().database
    record = (await database.find("node", {"id": app.id}))[0]
    record["context"]["lifecycle_state"] = state
    await database.save("node", record)
    current = await catalogue.get_or_compile_catalogue(workspace.id)
    assert current.generation_id != first.generation_id
    assert (
        next(cap for cap in current.capabilities if cap.app_id == app.id).availability
        == availability
    )
    assert await catalogue.get_or_compile_catalogue(workspace.id) is current


@pytest.mark.asyncio
async def test_schema_edit_does_not_mutate_published_snapshot_and_cold_call_rejects_old_generation(
    registered_catalogue,
):
    from app.api.errors import BadRequestError
    from app.schemas.governed_query import QuerySpec
    from app.services.governed_query.engine import execute_query

    owner, workspace, app, schema = registered_catalogue
    first = await catalogue.get_or_compile_catalogue(workspace.id)
    schema["properties"]["query"]["maxLength"] = 40
    published = next(cap for cap in first.capabilities if cap.app_id == app.id)
    assert "maxLength" not in published.input_schema["properties"]["query"]
    catalogue._ACTIVE_GENERATION.clear()
    with pytest.raises(BadRequestError, match="Stale capability"):
        await execute_query(
            user_id=owner.id,
            workspace_id=workspace.id,
            spec=QuerySpec(mode="core_open", resource="entry"),
            catalogue_generation=first.generation_id,
        )


@pytest.mark.asyncio
async def test_deleted_app_is_removed_even_with_stale_local_registry(
    registered_catalogue,
):
    _, workspace, app, _ = registered_catalogue
    first = await catalogue.get_or_compile_catalogue(workspace.id)
    await app.delete(cascade=False)
    current = await catalogue.get_or_compile_catalogue(workspace.id)
    assert current.generation_id != first.generation_id
    assert not any(cap.app_id == app.id for cap in current.capabilities)
    assert await catalogue.get_or_compile_catalogue(workspace.id) is current


@pytest.mark.asyncio
async def test_preview_does_not_overwrite_active_generation(registered_catalogue):
    from jvspatial.core.context import get_default_context

    from app.models.catalogue_generation import CatalogueGeneration

    _, workspace, _, _ = registered_catalogue
    active = await catalogue.get_or_compile_catalogue(workspace.id)
    preview = await catalogue.compile_workspace_catalogue(workspace.id, activate=False)
    assert preview.generation_id == active.generation_id
    rows = await get_default_context().database.find(
        "object",
        {
            "entity": CatalogueGeneration._entity_name(),
            "context.workspace_id": workspace.id,
        },
    )
    assert sorted(row["context"]["status"] for row in rows) == ["active", "failed"]
    assert await catalogue.get_or_compile_catalogue(workspace.id) is active


@pytest.mark.asyncio
async def test_primary_metadata_failure_never_returns_cached_success(
    registered_catalogue, monkeypatch
):
    from jvspatial.core.context import get_default_context

    from app.api.errors import QueryUnavailableError

    _, workspace, _, _ = registered_catalogue
    await catalogue.get_or_compile_catalogue(workspace.id)

    async def unavailable(*args, **kwargs):
        raise RuntimeError("primary source unavailable")

    monkeypatch.setattr(get_default_context().database, "find", unavailable)
    with pytest.raises(QueryUnavailableError):
        await catalogue.get_or_compile_catalogue(workspace.id)


@pytest.mark.asyncio
async def test_runtime_binding_changes_have_a_new_generation(registered_catalogue):
    from app.services.app_queries import registry

    _, workspace, app, _ = registered_catalogue
    before = await catalogue.get_or_compile_catalogue(workspace.id)
    registry._QUERIES[workspace.id][app.id]["lookup"][
        "handler_ref"
    ] = "example:new_handler"
    after = await catalogue.get_or_compile_catalogue(workspace.id)
    assert before.generation_id != after.generation_id


@pytest.mark.asyncio
async def test_declared_resolution_uses_current_visible_catalogue(monkeypatch):
    from app.schemas.governed_query import QuerySpec
    from app.services.governed_query import engine

    snapshot = CapabilityCatalogueSnapshot(
        workspace_id="ws",
        generation_id="generation",
        compiled_at="now",
        capabilities=[
            CapabilityDescriptor(
                namespace="first",
                key="report.daily",
                kind="query",
                name="Read",
                app_id="visible",
            ),
            CapabilityDescriptor(
                namespace="second",
                key="report.daily",
                kind="query",
                name="Read",
                app_id="hidden",
            ),
            CapabilityDescriptor(
                namespace="old",
                key="report.daily",
                kind="query",
                name="Read",
                app_id="paused",
                availability="paused",
            ),
        ],
    )
    monkeypatch.setattr(
        engine, "get_or_compile_catalogue", AsyncMock(return_value=snapshot)
    )

    async def role(_user, _kind, identity):
        return "viewer" if identity == "visible" else None

    monkeypatch.setattr(engine, "resolve_role", role)
    invoked = AsyncMock(
        return_value={
            "output": {"rows": [], "total_estimate": 0},
            "policy_revision": "current",
        }
    )
    monkeypatch.setattr("app.services.app_queries.dispatch.invoke_app_query", invoked)
    result = await engine._run_declared(
        user_id="caller",
        workspace_id="ws",
        spec=QuerySpec(mode="declared_capability", capability_key="report.daily"),
        catalogue_generation=None,
    )
    assert result.rows == [] and result.total_estimate == 0
    assert invoked.await_args.kwargs["app_id"] == "visible"
    assert invoked.await_args.kwargs["query_key"] == "report.daily"
    assert result.evidence.policy_revision == "current"
    with pytest.raises(BadRequestError) as caught:
        await engine._run_declared(
            user_id="caller",
            workspace_id="ws",
            spec=QuerySpec(
                mode="declared_capability",
                capability_key="wrong.report.daily",
                app_id="visible",
            ),
            catalogue_generation=None,
        )
    assert "not found" in str(caught.value)


def test_generic_result_references_use_local_routes_and_preserve_zero():
    from app.services.governed_query.engine import _refs_from_output, _rows_from_output

    output = {
        "rows": [
            {
                "id": "n.Entry.one",
                "title": "Record",
                "url_path": "https://untrusted.invalid",
            },
            {"id": "opaque-group"},
            {"id": "n.Entry.foreign", "workspace_id": "other"},
        ]
    }
    refs = _refs_from_output(output, workspace_id="ws", app_id="app")
    assert [ref.id for ref in refs] == ["n.Entry.one"]
    assert refs[0].url_path == "/entries/n.Entry.one"
    assert _rows_from_output({"entries": [], "total_estimate": 0}) == []
    assert _rows_from_output({"legacy_collection": [{"id": "n.Entry.one"}]}) == [
        {"id": "n.Entry.one"}
    ]


@pytest.mark.parametrize(
    "kind,authorization,expected",
    [
        ("read", {}, "app.read"),
        ("execute", {}, "entry.create"),
        ("propose", {}, "entry.create"),
        ("destructive", {}, "entry.create"),
        ("execute", {"targets": []}, "app:app:operation"),
    ],
)
def test_operation_advertisement_matches_dispatch_action(kind, authorization, expected):
    from app.services.app_operations.registry import operation_policy_action

    assert (
        operation_policy_action(
            "app", "operation", {"kind": kind, "authorization": authorization}
        )
        == expected
    )


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_catalogue_identity_and_lifecycle_across_real_processes(
    registered_catalogue,
):
    import asyncio
    import json
    import os
    import sys

    if os.getenv("INTEGRAL_TEST_DB") != "postgres":
        pytest.skip("Real multi-process qualification requires PostgreSQL")
    _, workspace, app, schema = registered_catalogue
    script = r"""
import asyncio,json,os,sys
from jvspatial import GraphContext,create_database
from jvspatial.core.context import set_default_context
from app.services.app_queries.registry import register_app_queries
from app.services.capability_catalogue.compile import get_or_compile_catalogue
async def main():
    db=create_database('postgres', dsn=os.environ['JVSPATIAL_POSTGRES_DSN'], min_size=1,max_size=2)
    set_default_context(GraphContext(database=db))
    register_app_queries(sys.argv[1],sys.argv[2],'test-extension',[{'key':'lookup','input_schema':json.loads(sys.argv[3]),'output_schema':{'type':'object'}}])
    snapshot=await get_or_compile_catalogue(sys.argv[1])
    print(json.dumps({'generation':snapshot.generation_id,'availability':[c.availability for c in snapshot.capabilities if c.app_id==sys.argv[2]]}))
    await db.close()
asyncio.run(main())
"""

    async def read_worker():
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            workspace.id,
            app.id,
            json.dumps(schema),
            cwd=str(Path(__file__).resolve().parents[1]),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=45)
        assert process.returncode == 0, stderr.decode()
        return json.loads(stdout.decode().splitlines()[-1])

    before = await catalogue.get_or_compile_catalogue(workspace.id)
    warm = await asyncio.gather(read_worker(), read_worker())
    assert {row["generation"] for row in warm} == {before.generation_id}
    app.lifecycle_state = "paused"
    await app.save()
    changed = await asyncio.gather(read_worker(), read_worker())
    assert len({row["generation"] for row in changed}) == 1
    assert changed[0]["generation"] != before.generation_id
    assert all(row["availability"] == ["paused"] for row in changed)
    refreshed = await catalogue.get_or_compile_catalogue(workspace.id)
    assert refreshed.generation_id == changed[0]["generation"]


@pytest.mark.asyncio
@pytest.mark.parametrize("total", [None, 0, 7])
async def test_declared_total_preserves_null_and_zero(monkeypatch, total):
    from app.schemas.governed_query import QuerySpec
    from app.services.governed_query import engine

    snapshot = CapabilityCatalogueSnapshot(
        workspace_id="ws",
        generation_id="gen",
        compiled_at="now",
        capabilities=[
            CapabilityDescriptor(
                namespace="test",
                key="lookup",
                kind="query",
                name="Lookup",
                app_id="app",
            )
        ],
    )
    monkeypatch.setattr(
        engine, "get_or_compile_catalogue", AsyncMock(return_value=snapshot)
    )
    monkeypatch.setattr(
        "app.services.app_queries.dispatch.invoke_app_query",
        AsyncMock(return_value={"output": {"rows": [], "total_estimate": total}}),
    )
    result = await engine._run_declared(
        user_id="user",
        workspace_id="ws",
        spec=QuerySpec(
            mode="declared_capability", capability_key="lookup", app_id="app"
        ),
        catalogue_generation=None,
    )
    assert result.total_estimate == total


@pytest.mark.asyncio
async def test_unbundled_app_name_is_canonical_catalogue_metadata(registered_catalogue):
    _, workspace, app, _ = registered_catalogue
    before = await catalogue.get_or_compile_catalogue(workspace.id)
    app.name = "Renamed App"
    await app.save()
    after = await catalogue.get_or_compile_catalogue(workspace.id)
    assert after.generation_id != before.generation_id
    assert {cap.namespace for cap in after.capabilities if cap.app_id == app.id} == {
        "renamed-app"
    }


def test_reserved_evidence_lists_do_not_replace_aggregate_rows():
    from app.services.governed_query.engine import _rows_from_output

    output = {"count": 7, "warnings": [], "object_refs": []}
    assert _rows_from_output(output) == [output]


@pytest.mark.asyncio
async def test_declared_partial_result_is_not_a_complete_zero(monkeypatch):
    from app.schemas.governed_query import QuerySpec
    from app.services.governed_query import engine

    snapshot = CapabilityCatalogueSnapshot(
        workspace_id="ws",
        generation_id="gen",
        compiled_at="now",
        capabilities=[
            CapabilityDescriptor(
                namespace="test",
                key="lookup",
                kind="query",
                name="Lookup",
                app_id="app",
            )
        ],
    )
    monkeypatch.setattr(
        engine, "get_or_compile_catalogue", AsyncMock(return_value=snapshot)
    )
    monkeypatch.setattr(
        "app.services.app_queries.dispatch.invoke_app_query",
        AsyncMock(
            return_value={
                "output": {
                    "items": [],
                    "complete": False,
                    "warnings": ["Only a partial audience was read."],
                }
            }
        ),
    )
    result = await engine._run_declared(
        user_id="user",
        workspace_id="ws",
        spec=QuerySpec(
            mode="declared_capability", capability_key="lookup", app_id="app"
        ),
        catalogue_generation=None,
    )
    assert result.total_estimate is None and result.degraded
    assert result.warnings == result.evidence.warnings
    assert "partial audience" in result.warnings[0]
