"""Boundary tests for the bounded QuerySpec contract."""

import asyncio
import base64
import hashlib
import hmac
import inspect
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from app.agentive.services.execution_runs import (
    build_capability_snapshot,
    build_core_capability_snapshot,
)
from app.agentive.services.query_spec import (
    QuerySpecError,
    QuerySpecExecutionError,
    execute_query_spec,
)
from app.agentive.tooling.catalogue import build_tool_catalogue
from app.config import settings
from app.exceptions import OperationalModelValidationError
from app.models.edges import CONTAINS, IS_MEMBER_OF, OWNS, REFERENCES
from app.models.nodes import App, Entry, Track, User, Workspace
from app.models.query_result_set import QueryResultSet
from app.schemas.capability_broker import ReceiptRef
from app.schemas.query_spec import (
    QueryFilter,
    QueryItemProvenance,
    QuerySort,
    QuerySpec,
    QuerySpecResult,
    QueryTraversal,
    validate_query_spec_semantics,
)
from app.services.operational_model_compile import compile_canonical_manifest


@pytest.mark.unit
def test_query_spec_accepts_a_bounded_entry_query() -> None:
    spec = QuerySpec(
        resource="entry",
        select=["id", "title", "status"],
        filters=[{"field": "status", "op": "eq", "value": "open"}],
        sort=[{"field": "updated_at", "direction": "desc"}],
        limit=20,
        cost_ceiling=100,
    )

    assert spec.resource == "entry"
    assert spec.filters == [QueryFilter(field="status", op="eq", value="open")]
    assert spec.sort == [QuerySort(field="updated_at", direction="desc")]


@pytest.mark.unit
def test_query_spec_accepts_explicit_business_field_paths_without_status_fallback() -> (
    None
):
    spec = QuerySpec(
        resource="entry",
        select=["id", "status", "custom_fields.status"],
        filters=[{"field": "custom_fields.status", "op": "is_null", "value": True}],
        sort=[{"field": "custom_fields.status", "direction": "asc"}],
    )

    validate_query_spec_semantics(spec)


@pytest.mark.unit
@pytest.mark.parametrize("resource", ["entry", "track", "app"])
def test_query_spec_accepts_only_supported_resources(resource: str) -> None:
    assert QuerySpec(resource=resource, select=["id"]).resource == resource

    with pytest.raises(ValidationError):
        QuerySpec(resource="workspace", select=["id"])


@pytest.mark.unit
def test_query_spec_rejects_unknown_filter_operator() -> None:
    with pytest.raises(ValidationError):
        QuerySpec(
            resource="entry",
            select=["id"],
            filters=[{"field": "status", "op": "matches", "value": "open"}],
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "operator",
    ["eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "is_null"],
)
def test_query_spec_public_schema_accepts_every_supported_operator(
    operator: str,
) -> None:
    value = (
        ["open"]
        if operator in {"in", "not_in"}
        else (True if operator == "is_null" else "open")
    )
    spec = QuerySpec.model_validate(
        {
            "resource": "entry",
            "select": ["id"],
            "filters": [{"field": "status", "op": operator, "value": value}],
        }
    )

    assert spec.filters[0].op == operator


@pytest.mark.unit
@pytest.mark.parametrize("value", [None, "true", 1])
def test_is_null_filter_requires_explicit_boolean(value: object) -> None:
    """The public model matches the executor and manifest is_null contract."""
    with pytest.raises(ValidationError):
        QueryFilter(field="status", op="is_null", value=value)


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    [
        {"nested": {"to": {"depth": [1]}}},
        ["plain", 1, 2.5, True, None],
    ],
)
def test_query_filter_accepts_bounded_json_values(value: object) -> None:
    assert QueryFilter(field="status", op="eq", value=value).value == value


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    [
        object(),
        {1: "non-string-key"},
        {"too": {"deep": {"for": {"query": {"value": 1}}}}},
        "x" * 8193,
        float("nan"),
    ],
)
def test_query_filter_rejects_non_json_deep_or_oversized_values(value: object) -> None:
    with pytest.raises(ValidationError):
        QueryFilter(field="status", op="eq", value=value)


@pytest.mark.unit
@pytest.mark.parametrize("operator", ["in", "not_in"])
def test_membership_filter_requires_at_most_one_hundred_list_items(
    operator: str,
) -> None:
    assert QueryFilter(field="status", op=operator, value=[1, 2]).value == [1, 2]

    for invalid in ((1, 2), list(range(101))):
        with pytest.raises(ValidationError):
            QueryFilter(field="status", op=operator, value=invalid)


@pytest.mark.unit
def test_query_spec_rejects_more_than_eight_filters() -> None:
    with pytest.raises(ValidationError):
        QuerySpec(
            resource="entry",
            select=["id"],
            filters=[
                {"field": f"field_{index}", "op": "eq", "value": index}
                for index in range(9)
            ],
        )


@pytest.mark.unit
def test_query_spec_rejects_more_than_two_sort_keys() -> None:
    with pytest.raises(ValidationError):
        QuerySpec(
            resource="track",
            select=["id"],
            sort=[
                {"field": "title", "direction": "asc"},
                {"field": "created_at", "direction": "desc"},
                {"field": "updated_at", "direction": "desc"},
            ],
        )


@pytest.mark.unit
def test_query_spec_bounds_root_and_traversal_projection_lengths() -> None:
    QuerySpec(resource="entry", select=[f"field_{index}" for index in range(20)])
    QueryTraversal(
        edge="references",
        select=[f"field_{index}" for index in range(12)],
    )

    with pytest.raises(ValidationError):
        QuerySpec(
            resource="entry",
            select=[f"field_{index}" for index in range(21)],
        )

    with pytest.raises(ValidationError):
        QueryTraversal(
            edge="references",
            select=[f"field_{index}" for index in range(13)],
        )


@pytest.mark.unit
def test_query_spec_strips_and_bounds_identifiers_and_cursor() -> None:
    spec = QuerySpec(
        resource="entry",
        select=[" id "],
        filters=[{"field": " status ", "op": "eq", "value": "open"}],
        sort=[{"field": " updated_at "}],
        traversal=[{"edge": " track ", "select": [" title "]}],
        cursor=" cursor-token ",
    )

    assert spec.select == ["id"]
    assert spec.filters[0].field == "status"
    assert spec.sort[0].field == "updated_at"
    assert spec.traversal[0].edge == "track"
    assert spec.traversal[0].select == ["title"]
    assert spec.cursor == "cursor-token"

    invalid_models = [
        (QueryFilter, {"field": " ", "op": "eq"}),
        (QuerySort, {"field": " "}),
        (QueryTraversal, {"edge": " "}),
        (QueryTraversal, {"edge": "track", "select": [" "]}),
        (QuerySpec, {"resource": "entry", "select": [" "]}),
        (QuerySpec, {"resource": "entry", "select": ["id"], "cursor": " "}),
        (QueryFilter, {"field": "x" * 129, "op": "eq"}),
        (QuerySort, {"field": "x" * 129}),
        (QueryTraversal, {"edge": "x" * 129}),
        (QueryTraversal, {"edge": "track", "select": ["x" * 129]}),
        (QuerySpec, {"resource": "entry", "select": ["x" * 129]}),
        (
            QuerySpec,
            {"resource": "entry", "select": ["id"], "cursor": "x" * 2049},
        ),
    ]

    for model, values in invalid_models:
        with pytest.raises(ValidationError):
            model.model_validate(values)


@pytest.mark.unit
@pytest.mark.parametrize("limit", [0, 101])
def test_query_spec_rejects_limit_outside_one_to_one_hundred(limit: int) -> None:
    with pytest.raises(ValidationError):
        QuerySpec(resource="app", select=["id"], limit=limit)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("limit", True),
        ("limit", "20"),
        ("cost_ceiling", True),
        ("cost_ceiling", "100"),
    ],
)
def test_query_spec_rejects_coerced_integer_bounds(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        QuerySpec.model_validate({"resource": "entry", "select": ["id"], field: value})


@pytest.mark.unit
def test_query_spec_rejects_more_than_one_traversal_hop() -> None:
    with pytest.raises(ValidationError):
        QuerySpec(
            resource="entry",
            select=["id"],
            traversal=[
                {"edge": "track", "select": ["id"]},
                {"edge": "app", "select": ["id"]},
            ],
        )

    with pytest.raises(ValidationError):
        QueryTraversal(edge="track", select=["id"], depth=2)


@pytest.mark.unit
@pytest.mark.parametrize("limit", [0, 21, True, "2"])
def test_query_traversal_limit_is_strictly_bounded(limit: object) -> None:
    with pytest.raises(ValidationError):
        QueryTraversal(edge="references", select=["id"], limit=limit)

    assert QueryTraversal(edge="references", select=["id"]).limit == 20


@pytest.mark.unit
@pytest.mark.parametrize("cost_ceiling", [0, 1001])
def test_query_spec_rejects_unbounded_cost_ceiling(cost_ceiling: int) -> None:
    with pytest.raises(ValidationError):
        QuerySpec(
            resource="entry",
            select=["id"],
            cost_ceiling=cost_ceiling,
        )


@pytest.mark.unit
def test_query_spec_result_accepts_optional_receipt_linkage() -> None:
    result = QuerySpecResult(
        items=[],
        result_set_id="result-1",
        normalized_plan={"resource": "entry"},
        graph_revision="sha256:def",
        item_provenance=[],
        redaction_state="none",
        receipt={
            "run_id": "run-1",
            "step_key": "step-1",
            "idempotency_key": "idem-1",
            "status": "completed",
            "capability_key": "integral_query_spec",
            "origin": "http",
        },
    )

    assert isinstance(result.receipt, ReceiptRef)
    assert result.receipt.run_id == "run-1"


@pytest.mark.unit
def test_all_query_models_forbid_extra_fields() -> None:
    valid_models = [
        (QueryFilter, {"field": "status", "op": "eq", "value": "open"}),
        (QuerySort, {"field": "updated_at", "direction": "desc"}),
        (QueryTraversal, {"edge": "track", "select": ["id"]}),
        (QuerySpec, {"resource": "entry", "select": ["id"]}),
        (
            QueryItemProvenance,
            {
                "item_id": "entry-1",
                "resource": "entry",
                "fingerprint": "sha256:abc",
            },
        ),
        (
            QuerySpecResult,
            {
                "items": [{"id": "entry-1"}],
                "result_set_id": "result-1",
                "normalized_plan": {"resource": "entry"},
                "graph_revision": "sha256:def",
                "item_provenance": [
                    {
                        "item_id": "entry-1",
                        "resource": "entry",
                        "fingerprint": "sha256:abc",
                    }
                ],
                "redaction_state": "none",
            },
        ),
    ]

    for model, values in valid_models:
        with pytest.raises(ValidationError):
            model.model_validate({**values, "unexpected": True})


@dataclass
class _QueryNode:
    id: str
    workspace_id: str = ""
    updated_at: str = "2026-09-18T12:00:00Z"
    title: str = ""
    status: str = "active"
    track_id: str = ""
    name: str = ""
    neighbors: dict[str, list["_QueryNode"]] = field(default_factory=dict)
    graph_calls: list[tuple[Any, str, Any, int]] = field(default_factory=list)
    edge_queries: list[tuple[Any, dict[str, Any]]] = field(default_factory=list)

    async def nodes(self, *, edge, direction="out", node=None, limit):
        self.graph_calls.append((edge[0], direction, node, limit))
        return self.neighbors.get(edge[0].__name__, [])[:limit]

    async def get_context(self):
        root = self

        class _Context:
            async def async_edge_iterator(self, edge_class, query):
                root.edge_queries.append((edge_class, query))
                outbound = isinstance(query.get("source"), str)
                allowed_ids = query["target" if outbound else "source"]["$in"]
                for neighbor in root.neighbors.get(edge_class.__name__, []):
                    if neighbor.id not in allowed_ids:
                        continue
                    yield type(
                        "_Edge",
                        (),
                        {
                            "source": root.id if outbound else neighbor.id,
                            "target": neighbor.id if outbound else root.id,
                        },
                    )()

        return _Context()


@pytest.mark.asyncio
async def test_query_spec_persists_metadata_without_raw_result_content(
    monkeypatch,
) -> None:
    entry = _QueryNode(
        id="entry-provenance",
        workspace_id="workspace-provenance",
        title="private result title",
    )
    entry.body = "private result body"
    entry.custom_fields = {"customer_secret": "private custom value"}
    entry.status = "private filter criterion"

    async def accessible_entries(_user_id, workspace_id=None, **_kwargs):
        assert workspace_id == "workspace-provenance"
        return [entry]

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries",
        accessible_entries,
    )

    result = await execute_query_spec(
        principal_id="user-provenance",
        workspace_id="workspace-provenance",
        run_id="run-provenance",
        idempotency_key="idem-provenance",
        spec=QuerySpec(
            resource="entry",
            select=["id", "title", "body", "custom_fields"],
            filters=[
                {
                    "field": "status",
                    "op": "eq",
                    "value": "private filter criterion",
                }
            ],
            limit=1,
        ),
    )

    records = list(
        await QueryResultSet.find(
            {
                "context.run_id": "run-provenance",
                "context.idempotency_key": "idem-provenance",
            }
        )
    )
    assert len(records) == 1
    record = records[0]
    assert record.result_set_id == result.result_set_id
    assert record.principal_id == "user-provenance"
    assert record.workspace_id == "workspace-provenance"
    assert record.normalized_plan == result.normalized_plan
    assert record.normalized_plan["filters"] == [
        {
            "field": "status",
            "op": "eq",
            "value_type": "string",
            "value_fingerprint": "hmac-sha256:"
            + hmac.new(
                settings.SECRET_KEY.encode("utf-8"),
                b'"private filter criterion"',
                hashlib.sha256,
            ).hexdigest(),
            "redacted": True,
        }
    ]
    canonical_plan = json.loads(json.dumps(record.normalized_plan))
    canonical_plan["filters"] = [
        {"field": "status", "op": "eq", "value": "private filter criterion"}
    ]
    canonical_plan["cost_model"].pop("edge_scans")
    canonical_plan["cost_model"].pop("actual_cost")
    fingerprint_source = json.dumps(
        {
            "canonical_plan": canonical_plan,
            "principal_id": "user-provenance",
            "workspace_id": "workspace-provenance",
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    assert record.plan_fingerprint == (
        "hmac-sha256:"
        + hmac.new(
            settings.SECRET_KEY.encode("utf-8"),
            fingerprint_source,
            hashlib.sha256,
        ).hexdigest()
    )
    assert record.graph_revision == result.graph_revision
    assert record.item_provenance == [
        {
            "item_id": "entry-provenance",
            "resource": "entry",
            "fingerprint": result.item_provenance[0].fingerprint,
        }
    ]
    assert record.item_fingerprints == [result.item_provenance[0].fingerprint]
    assert record.redaction_state == "none"
    assert record.created_at
    assert record.expires_at

    durable_json = record.model_dump_json()
    assert "private result title" not in durable_json
    assert "private result body" not in durable_json
    assert "private custom value" not in durable_json
    assert "private filter criterion" not in durable_json


@pytest.mark.asyncio
async def test_query_spec_idempotent_replay_reuses_result_set_identity(
    monkeypatch,
) -> None:
    entry = _QueryNode(id="entry-replay", workspace_id="workspace-replay")

    async def accessible_entries(_user_id, workspace_id=None, **_kwargs):
        return [entry]

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries",
        accessible_entries,
    )
    kwargs = {
        "principal_id": "user-replay",
        "workspace_id": "workspace-replay",
        "run_id": "run-replay",
        "idempotency_key": "idem-replay",
        "spec": QuerySpec(resource="entry", select=["id"], limit=1),
    }

    first = await execute_query_spec(**kwargs)
    record = await QueryResultSet.find_one(
        {
            "context.run_id": "run-replay",
            "context.idempotency_key": "idem-replay",
        }
    )
    original_fields = record.model_dump(mode="json")

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("graph access or save occurred")

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries", forbidden
    )
    monkeypatch.setattr(QueryResultSet, "save", forbidden)

    second = await execute_query_spec(**kwargs)

    assert first.result_set_id == second.result_set_id
    assert second.items is None
    assert second.replayed is True
    assert second.normalized_plan == record.normalized_plan
    assert second.graph_revision == record.graph_revision
    assert [item.model_dump(mode="json") for item in second.item_provenance] == (
        record.item_provenance
    )
    assert record.model_dump(mode="json") == original_fields
    records = list(
        await QueryResultSet.find(
            {
                "context.run_id": "run-replay",
                "context.idempotency_key": "idem-replay",
            }
        )
    )
    assert len(records) == 1


@pytest.mark.asyncio
async def test_query_spec_concurrent_idempotency_uses_atomic_result_set_winner(
    monkeypatch,
) -> None:
    entry = _QueryNode(id="entry-race", workspace_id="workspace-race")
    both_reading = asyncio.Event()
    readers = 0

    async def accessible_entries(_user_id, workspace_id=None, **_kwargs):
        nonlocal readers
        readers += 1
        if readers == 2:
            both_reading.set()
        await asyncio.wait_for(both_reading.wait(), timeout=1)
        return [entry]

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries",
        accessible_entries,
    )
    # Production startup creates this index before serving traffic. Warm it
    # explicitly so the concurrency assertion targets insert-if-absent rather
    # than racing first-use collection DDL in isolated Postgres test runs.
    context = await QueryResultSet().get_context()
    await context.ensure_indexes(QueryResultSet)
    first, second = await asyncio.gather(
        execute_query_spec(
            principal_id="user-race",
            workspace_id="workspace-race",
            run_id="run-race",
            idempotency_key="idem-race",
            spec=QuerySpec(resource="entry", select=["id"], limit=1),
        ),
        execute_query_spec(
            principal_id="user-race",
            workspace_id="workspace-race",
            run_id="run-race",
            idempotency_key="idem-race",
            spec=QuerySpec(resource="entry", select=["id"], limit=1),
        ),
    )

    assert first.result_set_id == second.result_set_id
    assert sorted([first.replayed, second.replayed]) == [False, True]
    records = list(
        await QueryResultSet.find(
            {
                "context.run_id": "run-race",
                "context.idempotency_key": "idem-race",
            }
        )
    )
    assert len(records) == 1


@pytest.mark.asyncio
async def test_query_spec_expired_result_is_deleted_and_replaced(monkeypatch) -> None:
    entry = _QueryNode(id="entry-expired", workspace_id="workspace-expired")
    graph_calls = 0

    async def accessible_entries(_user_id, workspace_id=None, **_kwargs):
        nonlocal graph_calls
        graph_calls += 1
        return [entry]

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries",
        accessible_entries,
    )
    kwargs = {
        "principal_id": "user-expired",
        "workspace_id": "workspace-expired",
        "run_id": "run-expired",
        "idempotency_key": "idem-expired",
        "spec": QuerySpec(resource="entry", select=["id"], limit=1),
    }
    first = await execute_query_spec(**kwargs)
    expired = await QueryResultSet.find_one(
        {
            "context.run_id": "run-expired",
            "context.idempotency_key": "idem-expired",
        }
    )
    expired.expires_at = "2000-01-01T00:00:00+00:00"
    await expired.save()

    original_delete = QueryResultSet.delete
    second_delete_entered = asyncio.Event()
    delete_calls = 0

    async def interleaved_delete(self):
        nonlocal delete_calls
        delete_calls += 1
        if delete_calls == 1:
            await asyncio.wait_for(second_delete_entered.wait(), timeout=1)
        else:
            second_delete_entered.set()
            await asyncio.sleep(0.05)
        return await original_delete(self)

    monkeypatch.setattr(QueryResultSet, "delete", interleaved_delete)
    second, concurrent_replay = await asyncio.gather(
        execute_query_spec(**kwargs),
        execute_query_spec(**kwargs),
    )

    assert graph_calls == 3
    # Either concurrent caller may win the replacement claim after both have
    # observed the expired receipt. The contract is exactly one fresh result
    # and one replay, not scheduler-dependent caller ordering.
    fresh, replay = (
        (second, concurrent_replay)
        if not second.replayed
        else (concurrent_replay, second)
    )
    assert fresh.replayed is False
    assert fresh.items == [{"id": "entry-expired"}]
    assert replay.replayed is True
    assert replay.items is None
    assert replay.result_set_id == fresh.result_set_id
    assert fresh.result_set_id != first.result_set_id
    records = list(
        await QueryResultSet.find(
            {
                "context.run_id": "run-expired",
                "context.idempotency_key": "idem-expired",
            }
        )
    )
    assert len(records) == 1
    assert records[0].result_set_id == fresh.result_set_id


@pytest.mark.asyncio
async def test_query_spec_idempotency_identity_includes_principal_and_workspace(
    monkeypatch,
) -> None:
    async def accessible_entries(user_id, workspace_id=None, **_kwargs):
        return [_QueryNode(id=f"entry-{user_id}", workspace_id=workspace_id)]

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries",
        accessible_entries,
    )
    spec = QuerySpec(resource="entry", select=["id"], limit=1)

    first = await execute_query_spec(
        principal_id="user-a",
        workspace_id="workspace-a",
        run_id="shared-run",
        idempotency_key="shared-key",
        spec=spec,
    )
    second = await execute_query_spec(
        principal_id="user-b",
        workspace_id="workspace-b",
        run_id="shared-run",
        idempotency_key="shared-key",
        spec=spec,
    )

    assert first.replayed is False
    assert second.replayed is False
    assert first.items == [{"id": "entry-user-a"}]
    assert second.items == [{"id": "entry-user-b"}]


@pytest.mark.asyncio
async def test_query_spec_idempotency_conflict_rejects_before_graph_access(
    monkeypatch,
) -> None:
    entry = _QueryNode(id="entry-conflict", workspace_id="workspace-conflict")

    async def accessible_entries(_user_id, workspace_id=None, **_kwargs):
        return [entry]

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries",
        accessible_entries,
    )
    common = {
        "principal_id": "user-conflict",
        "workspace_id": "workspace-conflict",
        "run_id": "run-conflict",
        "idempotency_key": "idem-conflict",
    }
    await execute_query_spec(
        **common,
        spec=QuerySpec(resource="entry", select=["id"], limit=1),
    )
    record = await QueryResultSet.find_one(
        {
            "context.run_id": "run-conflict",
            "context.idempotency_key": "idem-conflict",
        }
    )
    original_fields = record.model_dump(mode="json")

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("graph access or save occurred")

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries", forbidden
    )
    monkeypatch.setattr(QueryResultSet, "save", forbidden)

    with pytest.raises(QuerySpecError, match="query.idempotency_conflict"):
        await execute_query_spec(
            **common,
            spec=QuerySpec(resource="entry", select=["id", "title"], limit=1),
        )

    unchanged = await QueryResultSet.find_one(
        {
            "context.run_id": "run-conflict",
            "context.idempotency_key": "idem-conflict",
        }
    )
    assert unchanged.model_dump(mode="json") == original_fields


@pytest.mark.unit
def test_query_result_set_declares_required_indexes() -> None:
    indexed_fields = {
        index["field"].removeprefix("context.")
        for index in QueryResultSet.get_indexes()
        if index.get("field", "").startswith("context.")
    }

    assert {
        "result_set_id",
        "run_id",
        "principal_id",
        "workspace_id",
        "idempotency_key",
        "plan_fingerprint",
    } <= indexed_fields


@pytest.mark.unit
def test_query_result_set_indexes_are_registered_at_startup() -> None:
    from app import main

    source = inspect.getsource(main._ensure_model_indexes)

    assert "ensure_indexes(QueryResultSet)" in source


@pytest.mark.unit
@pytest.mark.asyncio
async def test_query_spec_scopes_roots_before_query_operations(monkeypatch) -> None:
    entry_a = _QueryNode(
        id="entry-a",
        workspace_id="workspace-a",
        title="Allowed",
        track_id="track-a",
    )
    entry_b = _QueryNode(
        id="entry-b",
        workspace_id="workspace-b",
        title="Secret",
        track_id="track-b",
    )

    async def accessible_entries(user_id, workspace_id=None, **_kwargs):
        assert workspace_id in {"workspace-a", "workspace-b"}
        return [entry_a] if user_id == "user-a" else [entry_b]

    async def no_tracks(_user_id):
        return []

    async def no_apps(_user_id):
        return []

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries",
        accessible_entries,
    )
    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_tracks", no_tracks
    )
    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_apps", no_apps
    )

    result_a = await execute_query_spec(
        principal_id="user-a",
        workspace_id="workspace-a",
        spec=QuerySpec(resource="entry", select=["id", "title"], limit=20),
    )
    result_b = await execute_query_spec(
        principal_id="user-b",
        workspace_id="workspace-b",
        spec=QuerySpec(resource="entry", select=["id", "title"], limit=20),
    )

    assert {row["id"] for row in result_a.items} == {"entry-a"}
    assert "entry-b" not in result_a.model_dump_json()
    assert {row["id"] for row in result_b.items} == {"entry-b"}
    assert "entry-a" not in result_b.model_dump_json()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "spec",
    [
        QuerySpec(resource="entry", select=["password_hash"]),
        QuerySpec(
            resource="entry",
            select=["id"],
            filters=[{"field": "unknown", "op": "eq", "value": "x"}],
        ),
        QuerySpec(
            resource="entry",
            select=["id"],
            sort=[{"field": "unknown"}],
        ),
        QuerySpec(
            resource="entry",
            select=["id"],
            traversal=[{"edge": "app", "select": ["id"]}],
        ),
        QuerySpec(
            resource="entry",
            select=["id"],
            traversal=[{"edge": "track", "select": ["password_hash"]}],
        ),
    ],
)
async def test_invalid_query_plan_rejects_before_permission_or_graph_access(
    monkeypatch, spec
) -> None:
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("permission or graph access occurred")

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries", forbidden
    )
    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_tracks", forbidden
    )
    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_apps", forbidden
    )

    with pytest.raises(QuerySpecExecutionError, match="not allowed"):
        await execute_query_spec(
            principal_id="user-a",
            workspace_id="workspace-a",
            spec=spec,
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cost_ceiling_rejects_predictably_before_graph_access(
    monkeypatch,
) -> None:
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("permission or graph access occurred")

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries", forbidden
    )

    with pytest.raises(
        QuerySpecExecutionError,
        match=r"estimated cost 1300 exceeds cost ceiling 5",
    ):
        await execute_query_spec(
            principal_id="user-a",
            workspace_id="workspace-a",
            spec=QuerySpec(
                resource="entry",
                select=["id", "title"],
                filters=[{"field": "status", "op": "eq", "value": "active"}],
                sort=[{"field": "updated_at"}],
                traversal=[{"edge": "references", "select": ["id"]}],
                limit=50,
                cost_ceiling=5,
            ),
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_traversal_cost_accounts_for_worst_case_root_fanout(
    monkeypatch,
) -> None:
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("permission or graph access occurred")

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries", forbidden
    )

    with pytest.raises(
        QuerySpecError,
        match=r"estimated cost 2100 exceeds cost ceiling 1000",
    ):
        await execute_query_spec(
            principal_id="user-a",
            workspace_id="workspace-a",
            spec=QuerySpec(
                resource="entry",
                select=["id"],
                traversal=[
                    {
                        "edge": "references",
                        "select": ["id"],
                        "limit": 20,
                    }
                ],
                limit=100,
                cost_ceiling=1000,
            ),
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_authorized_scan_cap_rejects_before_filter_or_traversal(
    monkeypatch,
) -> None:
    candidates = [_QueryNode(id=f"entry-{index}") for index in range(1001)]

    async def accessible_entries(_user_id, workspace_id=None, **_kwargs):
        return candidates

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries",
        accessible_entries,
    )

    with pytest.raises(
        QuerySpecError,
        match=r"authorized entry scan 1001 exceeds limit 1000",
    ):
        await execute_query_spec(
            principal_id="user-a",
            workspace_id="workspace-a",
            spec=QuerySpec(
                resource="entry",
                select=["id"],
                filters=[{"field": "status", "op": "eq", "value": "active"}],
                traversal=[{"edge": "references", "select": ["id"], "limit": 1}],
                limit=1,
                cost_ceiling=1000,
            ),
        )

    assert all(not candidate.graph_calls for candidate in candidates)
    assert all(not candidate.edge_queries for candidate in candidates)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dynamic_cost_uses_actual_authorized_candidate_count(
    monkeypatch,
) -> None:
    candidates = [_QueryNode(id=f"entry-{index}") for index in range(101)]

    async def accessible_entries(_user_id, workspace_id=None, **_kwargs):
        return candidates

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries",
        accessible_entries,
    )

    with pytest.raises(
        QuerySpecError,
        match=r"dynamic estimated cost 101 exceeds cost ceiling 100",
    ):
        await execute_query_spec(
            principal_id="user-a",
            workspace_id="workspace-a",
            spec=QuerySpec(
                resource="entry",
                select=["id"],
                limit=1,
                cost_ceiling=100,
            ),
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_actual_traversal_cost_rejects_before_edge_iteration_then_proceeds(
    monkeypatch,
) -> None:
    source = _QueryNode(id="entry-0-source")
    targets = [_QueryNode(id=f"entry-{index}") for index in range(10)]
    source.neighbors[REFERENCES.__name__] = [targets[0]]
    candidates = [source, *targets]

    async def accessible_entries(_user_id, workspace_id=None, **_kwargs):
        return candidates

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries",
        accessible_entries,
    )
    base_spec = {
        "resource": "entry",
        "select": ["id"],
        "filters": [{"field": "id", "op": "eq", "value": source.id}],
        "traversal": [{"edge": "references", "select": ["id"], "limit": 1}],
        "limit": 1,
    }

    with pytest.raises(
        QuerySpecError,
        match=r"dynamic estimated cost 44 exceeds cost ceiling 40",
    ):
        await execute_query_spec(
            principal_id="user-a",
            workspace_id="workspace-a",
            spec=QuerySpec.model_validate({**base_spec, "cost_ceiling": 40}),
        )
    assert source.edge_queries == []

    result = await execute_query_spec(
        principal_id="user-a",
        workspace_id="workspace-a",
        spec=QuerySpec.model_validate({**base_spec, "cost_ceiling": 50}),
    )

    assert result.items == [{"id": "entry-0-source", "references": [{"id": "entry-0"}]}]
    assert len(source.edge_queries) == 1
    assert "page_root_count" in result.normalized_plan["cost_model"]["formula"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_query_spec_filters_projects_sorts_and_binds_cursor(monkeypatch) -> None:
    entries = [
        _QueryNode(id="entry-2", title="Alpha two", status="open", track_id="track-a"),
        _QueryNode(id="entry-1", title="Alpha one", status="open", track_id="track-a"),
        _QueryNode(id="entry-3", title="Beta", status="closed", track_id="track-a"),
    ]

    async def accessible_entries(_user_id, workspace_id=None, **_kwargs):
        assert workspace_id == "workspace-a"
        return entries

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries",
        accessible_entries,
    )

    first = await execute_query_spec(
        principal_id="user-a",
        workspace_id="workspace-a",
        spec=QuerySpec(
            resource="entry",
            select=["id", "title"],
            filters=[
                {"field": "status", "op": "in", "value": ["open", "pending"]},
                {"field": "title", "op": "contains", "value": "a"},
                {"field": "status", "op": "ne", "value": "closed"},
            ],
            sort=[{"field": "title", "direction": "asc"}],
            limit=1,
            cost_ceiling=100,
        ),
    )
    second = await execute_query_spec(
        principal_id="user-a",
        workspace_id="workspace-a",
        spec=QuerySpec(
            resource="entry",
            select=["id", "title"],
            filters=[
                {"field": "status", "op": "in", "value": ["open", "pending"]},
                {"field": "title", "op": "contains", "value": "a"},
                {"field": "status", "op": "ne", "value": "closed"},
            ],
            sort=[{"field": "title", "direction": "asc"}],
            limit=1,
            cost_ceiling=100,
            cursor=first.next_cursor,
        ),
    )

    assert first.items == [{"id": "entry-1", "title": "Alpha one"}]
    assert second.items == [{"id": "entry-2", "title": "Alpha two"}]
    assert first.result_set_id != second.result_set_id
    assert first.graph_revision.startswith("sha256:")
    assert first.item_provenance[0].item_id == "entry-1"
    assert first.redaction_state == "none"

    with pytest.raises(QuerySpecExecutionError, match="cursor does not match"):
        await execute_query_spec(
            principal_id="user-a",
            workspace_id="workspace-a",
            spec=QuerySpec(
                resource="entry",
                select=["id"],
                limit=1,
                cursor=first.next_cursor,
            ),
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_traversal_intersects_workspace_authorized_target_set(
    monkeypatch,
) -> None:
    allowed_reference = _QueryNode(
        id="entry-allowed",
        title="Allowed reference",
        track_id="track-a",
    )
    forbidden_reference = _QueryNode(
        id="entry-forbidden",
        workspace_id="workspace-b",
        title="Forbidden reference",
        track_id="track-b",
    )
    source = _QueryNode(id="entry-0-source", title="Source", track_id="track-a")
    second_root = _QueryNode(id="entry-z-root", title="Later", track_id="track-a")
    source.neighbors[REFERENCES.__name__] = [
        forbidden_reference,
        allowed_reference,
    ]

    async def accessible_entries(_user_id, workspace_id=None, **_kwargs):
        assert workspace_id == "workspace-a"
        return [source, allowed_reference, second_root]

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries",
        accessible_entries,
    )

    result = await execute_query_spec(
        principal_id="user-a",
        workspace_id="workspace-a",
        spec=QuerySpec(
            resource="entry",
            select=["id"],
            traversal=[
                {
                    "edge": "references",
                    "select": ["id", "title"],
                    "limit": 1,
                }
            ],
            limit=1,
            cost_ceiling=100,
        ),
    )

    assert result.items == [
        {
            "id": "entry-0-source",
            "references": [{"id": "entry-allowed", "title": "Allowed reference"}],
        }
    ]
    assert "entry-forbidden" not in result.model_dump_json()
    assert result.next_cursor
    assert "entry-forbidden" not in result.next_cursor
    assert source.graph_calls == []
    assert source.edge_queries == [
        (
            REFERENCES,
            {
                "entity": "REFERENCES",
                "source": "entry-0-source",
                "target": {"$in": ["entry-0-source", "entry-allowed", "entry-z-root"]},
            },
        )
    ]
    assert {(item.resource, item.item_id) for item in result.item_provenance} == {
        ("entry", "entry-0-source"),
        ("entry", "entry-allowed"),
    }
    assert len(result.item_provenance) == 2


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operator", "value"),
    [
        ("eq", 5),
        ("ne", 4),
        ("gt", 4),
        ("gte", 5),
        ("lt", 6),
        ("lte", 5),
        ("in", [4, 5]),
        ("not_in", [6, 7]),
        ("contains", "lph"),
        ("is_null", False),
    ],
)
async def test_query_spec_executes_every_schema_operator(
    monkeypatch, operator: str, value: object
) -> None:
    item = _QueryNode(id="entry-1", title="Alpha")
    item.status = 5

    async def accessible_entries(_user_id, workspace_id=None, **_kwargs):
        assert workspace_id == "workspace-a"
        return [item]

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries",
        accessible_entries,
    )
    field_name = "title" if operator == "contains" else "status"
    result = await execute_query_spec(
        principal_id="user-a",
        workspace_id="workspace-a",
        spec=QuerySpec.model_validate(
            {
                "resource": "entry",
                "select": ["id"],
                "filters": [{"field": field_name, "op": operator, "value": value}],
                "limit": 1,
            }
        ),
    )

    assert result.items == [{"id": "entry-1"}]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_is_null_filter_supports_true(monkeypatch) -> None:
    item = _QueryNode(id="entry-1")
    item.updated_at = None

    async def accessible_entries(_user_id, workspace_id=None, **_kwargs):
        return [item]

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries",
        accessible_entries,
    )

    result = await execute_query_spec(
        principal_id="user-a",
        workspace_id="workspace-a",
        spec=QuerySpec(
            resource="entry",
            select=["id"],
            filters=[{"field": "updated_at", "op": "is_null", "value": True}],
            limit=1,
        ),
    )

    assert result.items == [{"id": "entry-1"}]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cursor_is_bound_to_principal_and_workspace(monkeypatch) -> None:
    entries = [_QueryNode(id="entry-1"), _QueryNode(id="entry-2")]

    async def accessible_entries(_user_id, workspace_id=None, **_kwargs):
        return entries

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries",
        accessible_entries,
    )
    first = await execute_query_spec(
        principal_id="user-a",
        workspace_id="workspace-a",
        spec=QuerySpec(resource="entry", select=["id"], limit=1),
    )

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("permission access occurred")

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries", forbidden
    )
    for principal_id, workspace_id in [
        ("user-b", "workspace-a"),
        ("user-a", "workspace-b"),
    ]:
        with pytest.raises(QuerySpecError, match="cursor does not match"):
            await execute_query_spec(
                principal_id=principal_id,
                workspace_id=workspace_id,
                spec=QuerySpec(
                    resource="entry",
                    select=["id"],
                    limit=1,
                    cursor=first.next_cursor,
                ),
            )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_signed_keyset_cursor_is_stable_when_rows_insert_before_it(
    monkeypatch,
) -> None:
    app_two = _QueryNode(id="app-2", workspace_id="workspace-a")
    app_ten = _QueryNode(id="app-10", workspace_id="workspace-a")
    app_two.position = 2
    app_ten.position = 10
    apps = [app_ten, app_two]

    async def accessible_apps(_user_id):
        return apps

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_apps",
        accessible_apps,
    )
    query = {
        "resource": "app",
        "select": ["id"],
        "sort": [{"field": "position", "direction": "asc"}],
        "limit": 1,
    }
    first = await execute_query_spec(
        principal_id="user-a",
        workspace_id="workspace-a",
        spec=QuerySpec.model_validate(query),
    )
    inserted = _QueryNode(id="app-1", workspace_id="workspace-a")
    inserted.position = 1
    apps.append(inserted)

    second = await execute_query_spec(
        principal_id="user-a",
        workspace_id="workspace-a",
        spec=QuerySpec.model_validate({**query, "cursor": first.next_cursor}),
    )

    assert first.items == [{"id": "app-2"}]
    assert second.items == [{"id": "app-10"}]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_signed_keyset_cursor_rejects_payload_tampering(monkeypatch) -> None:
    apps = [
        _QueryNode(id="app-1", workspace_id="workspace-a"),
        _QueryNode(id="app-2", workspace_id="workspace-a"),
    ]

    async def accessible_apps(_user_id):
        return apps

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_apps",
        accessible_apps,
    )
    first = await execute_query_spec(
        principal_id="user-a",
        workspace_id="workspace-a",
        spec=QuerySpec(resource="app", select=["id"], limit=1),
    )
    padded = first.next_cursor + ("=" * (-len(first.next_cursor) % 4))
    decoded = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    decoded["payload"]["id"] = "app-tampered"
    tampered = (
        base64.urlsafe_b64encode(
            json.dumps(decoded, sort_keys=True, separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )

    with pytest.raises(QuerySpecError, match="invalid query cursor signature"):
        await execute_query_spec(
            principal_id="user-a",
            workspace_id="workspace-a",
            spec=QuerySpec(
                resource="app",
                select=["id"],
                limit=1,
                cursor=tampered,
            ),
        )


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cursor",
    [
        "not-base64!",
        "W10",  # base64url encoding of []
    ],
)
async def test_malformed_cursor_has_deterministic_query_spec_error(
    monkeypatch, cursor: str
) -> None:
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("permission access occurred")

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries", forbidden
    )

    with pytest.raises(QuerySpecError, match="invalid query cursor"):
        await execute_query_spec(
            principal_id="user-a",
            workspace_id="workspace-a",
            spec=QuerySpec(
                resource="entry",
                select=["id"],
                limit=1,
                cursor=cursor,
            ),
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_traversal_rejects_wrong_fixed_direction_before_access(
    monkeypatch,
) -> None:
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("permission or graph access occurred")

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries", forbidden
    )

    with pytest.raises(QuerySpecError, match="direction 'out' is not allowed"):
        await execute_query_spec(
            principal_id="user-a",
            workspace_id="workspace-a",
            spec=QuerySpec(
                resource="entry",
                select=["id"],
                traversal=[
                    {
                        "edge": "track",
                        "direction": "out",
                        "select": ["id"],
                        "limit": 1,
                    }
                ],
                limit=1,
            ),
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_traversal_normalized_direction_matches_execution(monkeypatch) -> None:
    track = _QueryNode(id="track-1", workspace_id="workspace-a")
    source = _QueryNode(id="entry-1", track_id="track-1")
    source.neighbors[CONTAINS.__name__] = [track]

    async def accessible_entries(_user_id, workspace_id=None, **_kwargs):
        return [source]

    async def accessible_tracks(_user_id):
        return [track]

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries",
        accessible_entries,
    )
    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_tracks",
        accessible_tracks,
    )

    result = await execute_query_spec(
        principal_id="user-a",
        workspace_id="workspace-a",
        spec=QuerySpec(
            resource="entry",
            select=["id"],
            traversal=[
                {
                    "edge": "track",
                    "direction": "in",
                    "select": ["id"],
                    "limit": 1,
                }
            ],
            limit=1,
        ),
    )

    assert result.normalized_plan["traversal"][0]["direction"] == "in"
    assert source.graph_calls == []
    assert source.edge_queries == [
        (
            CONTAINS,
            {
                "entity": "CONTAINS",
                "source": {"$in": ["track-1"]},
                "target": "entry-1",
            },
        )
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_traversal_results_repeat_in_authorized_target_id_order(
    monkeypatch,
) -> None:
    source = _QueryNode(id="entry-0-source")
    target_a = _QueryNode(id="entry-a", title="A")
    target_b = _QueryNode(id="entry-b", title="B")
    source.neighbors[REFERENCES.__name__] = [target_b, target_a, target_a]

    async def accessible_entries(_user_id, workspace_id=None, **_kwargs):
        return [source, target_a, target_b]

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries",
        accessible_entries,
    )
    spec = QuerySpec(
        resource="entry",
        select=["id"],
        traversal=[{"edge": "references", "select": ["id"], "limit": 2}],
        limit=1,
    )

    first = await execute_query_spec(
        principal_id="user-a",
        workspace_id="workspace-a",
        spec=spec,
    )
    source.neighbors[REFERENCES.__name__].reverse()
    second = await execute_query_spec(
        principal_id="user-a",
        workspace_id="workspace-a",
        spec=spec,
    )

    expected = [{"id": "entry-a"}, {"id": "entry-b"}]
    assert first.items[0]["references"] == expected
    assert second.items[0]["references"] == expected
    assert {(item.resource, item.item_id) for item in first.item_provenance} == {
        ("entry", "entry-0-source"),
        ("entry", "entry-a"),
        ("entry", "entry-b"),
    }
    assert len(first.item_provenance) == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_global_edge_scan_budget_counts_parallel_edges_across_roots(
    monkeypatch,
) -> None:
    source_a = _QueryNode(id="entry-0-source")
    source_b = _QueryNode(id="entry-1-source")
    target_a = _QueryNode(id="entry-y-target")
    target_b = _QueryNode(id="entry-z-target")
    source_a.neighbors[REFERENCES.__name__] = [target_a] * 5
    source_b.neighbors[REFERENCES.__name__] = [target_b] * 5
    candidates = [source_a, source_b, target_a, target_b]

    async def accessible_entries(_user_id, workspace_id=None, **_kwargs):
        return candidates

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries",
        accessible_entries,
    )
    base_spec = {
        "resource": "entry",
        "select": ["id"],
        "traversal": [{"edge": "references", "select": ["id"], "limit": 2}],
        "limit": 2,
    }

    with pytest.raises(
        QuerySpecError,
        match=r"query\.cost_exceeded: actual cost 13 exceeds cost ceiling 12",
    ):
        await execute_query_spec(
            principal_id="user-a",
            workspace_id="workspace-a",
            spec=QuerySpec.model_validate({**base_spec, "cost_ceiling": 12}),
        )

    result = await execute_query_spec(
        principal_id="user-a",
        workspace_id="workspace-a",
        spec=QuerySpec.model_validate({**base_spec, "cost_ceiling": 14}),
    )

    assert result.items == [
        {"id": source_a.id, "references": [{"id": target_a.id}]},
        {"id": source_b.id, "references": [{"id": target_b.id}]},
    ]
    assert len(result.item_provenance) == 4
    assert result.normalized_plan["cost_model"]["edge_scans"] == 10
    assert result.normalized_plan["cost_model"]["actual_cost"] == 14


@pytest.mark.unit
@pytest.mark.asyncio
async def test_traversal_edge_scan_rejects_overflow_predictably(
    monkeypatch,
) -> None:
    source = _QueryNode(id="entry-0-source")
    target = _QueryNode(id="entry-target")
    source.neighbors[REFERENCES.__name__] = [target] * 1001

    async def accessible_entries(_user_id, workspace_id=None, **_kwargs):
        return [source, target]

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries",
        accessible_entries,
    )
    monkeypatch.setattr(
        "app.agentive.services.query_spec.MAX_AUTHORIZED_SCAN",
        5,
    )

    with pytest.raises(
        QuerySpecError,
        match=r"authorized traversal edge scan exceeds limit 5",
    ):
        await execute_query_spec(
            principal_id="user-a",
            workspace_id="workspace-a",
            spec=QuerySpec(
                resource="entry",
                select=["id"],
                traversal=[{"edge": "references", "select": ["id"], "limit": 1}],
                limit=1,
            ),
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sort_preserves_native_numeric_order_with_deterministic_mixed_nulls(
    monkeypatch,
) -> None:
    apps = [
        _QueryNode(id="app-10", workspace_id="workspace-a"),
        _QueryNode(id="app-2", workspace_id="workspace-a"),
        _QueryNode(id="app-string", workspace_id="workspace-a"),
        _QueryNode(id="app-null", workspace_id="workspace-a"),
    ]
    for app, position in zip(apps, [10, 2, "3", None]):
        app.position = position

    async def accessible_apps(_user_id):
        return apps

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_apps",
        accessible_apps,
    )

    async def sorted_ids(direction: str) -> list[str]:
        result = await execute_query_spec(
            principal_id="user-a",
            workspace_id="workspace-a",
            spec=QuerySpec(
                resource="app",
                select=["id"],
                sort=[{"field": "position", "direction": direction}],
                limit=10,
            ),
        )
        return [row["id"] for row in result.items]

    assert await sorted_ids("asc") == [
        "app-2",
        "app-10",
        "app-string",
        "app-null",
    ]
    assert await sorted_ids("desc") == [
        "app-string",
        "app-10",
        "app-2",
        "app-null",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sort_preserves_native_date_order(monkeypatch) -> None:
    older = _QueryNode(id="app-older", workspace_id="workspace-a")
    newer = _QueryNode(id="app-newer", workspace_id="workspace-a")
    older.created_at = date(2025, 12, 31)
    newer.created_at = date(2026, 1, 1)

    async def accessible_apps(_user_id):
        return [newer, older]

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_apps",
        accessible_apps,
    )
    result = await execute_query_spec(
        principal_id="user-a",
        workspace_id="workspace-a",
        spec=QuerySpec(
            resource="app",
            select=["id"],
            sort=[{"field": "created_at", "direction": "asc"}],
            limit=10,
        ),
    )

    assert [row["id"] for row in result.items] == ["app-older", "app-newer"]


@pytest.mark.asyncio
async def test_query_spec_real_graph_isolates_users_and_workspaces() -> None:
    from app.services.app_graph import (
        catalog_app,
        catalog_track,
        catalog_user,
        catalog_workspace,
    )
    from app.utils.time import utc_now_iso

    now = utc_now_iso()
    user_a = await User.create(
        user_id="query-real-user-a",
        display_name="Query User A",
        created_at=now,
        updated_at=now,
    )
    user_b = await User.create(
        user_id="query-real-user-b",
        display_name="Query User B",
        created_at=now,
        updated_at=now,
    )
    await catalog_user(user_a)
    await catalog_user(user_b)

    workspace_a = await Workspace.create(
        kind="personal",
        workspace_type="personal",
        name="Query Workspace A",
        name_fold="query workspace a",
        created_at=now,
        updated_at=now,
    )
    workspace_b = await Workspace.create(
        kind="personal",
        workspace_type="personal",
        name="Query Workspace B",
        name_fold="query workspace b",
        created_at=now,
        updated_at=now,
    )
    await user_a.connect(workspace_a, edge=IS_MEMBER_OF, role="owner", joined_at=now)
    await user_b.connect(workspace_b, edge=IS_MEMBER_OF, role="owner", joined_at=now)
    await catalog_workspace(workspace_a)
    await catalog_workspace(workspace_b)

    app_a = await App.create(
        name="Query App A",
        owner_user_id=user_a.id,
        workspace_id=workspace_a.id,
        visibility="private",
        created_at=now,
        updated_at=now,
    )
    app_b = await App.create(
        name="Query App B secret",
        owner_user_id=user_b.id,
        workspace_id=workspace_b.id,
        visibility="private",
        created_at=now,
        updated_at=now,
    )
    await user_a.connect(app_a, edge=OWNS, role="owner", granted_at=now)
    await user_b.connect(app_b, edge=OWNS, role="owner", granted_at=now)
    await catalog_app(app_a)
    await catalog_app(app_b)

    track_a = await Track.create(
        title="Query Track A",
        owner_id=user_a.id,
        workspace_id=workspace_a.id,
        visibility="private",
        created_at=now,
        updated_at=now,
    )
    track_b = await Track.create(
        title="Query Track B secret",
        owner_id=user_b.id,
        workspace_id=workspace_b.id,
        visibility="private",
        created_at=now,
        updated_at=now,
    )
    await user_a.connect(track_a, edge=OWNS, role="owner", granted_at=now)
    await user_b.connect(track_b, edge=OWNS, role="owner", granted_at=now)
    await app_a.connect(track_a, edge=CONTAINS, added_at=now)
    await app_b.connect(track_b, edge=CONTAINS, added_at=now)
    await catalog_track(track_a)
    await catalog_track(track_b)

    entry_a1 = await Entry.create(
        title="Query A first",
        body="authorized-a-one",
        author_id=user_a.id,
        track_id=track_a.id,
        created_at=now,
        updated_at=now,
    )
    entry_a2 = await Entry.create(
        title="Query A second",
        body="authorized-a-two",
        author_id=user_a.id,
        track_id=track_a.id,
        created_at=now,
        updated_at=now,
    )
    entry_b = await Entry.create(
        title="Query B secret",
        body="forbidden-b-content",
        author_id=user_b.id,
        track_id=track_b.id,
        created_at=now,
        updated_at=now,
    )
    await track_a.connect(entry_a1, edge=CONTAINS, added_at=now)
    await track_a.connect(entry_a2, edge=CONTAINS, added_at=now)
    await track_b.connect(entry_b, edge=CONTAINS, added_at=now)
    await entry_a1.connect(entry_b, edge=REFERENCES)
    await entry_a1.connect(entry_a2, edge=REFERENCES)

    traversed = await execute_query_spec(
        principal_id=user_a.id,
        workspace_id=workspace_a.id,
        spec=QuerySpec(
            resource="entry",
            select=["id"],
            filters=[{"field": "id", "op": "eq", "value": entry_a1.id}],
            traversal=[
                {
                    "edge": "references",
                    "select": ["id", "title", "body"],
                    "limit": 1,
                }
            ],
            limit=1,
        ),
    )
    assert traversed.items == [
        {
            "id": entry_a1.id,
            "references": [
                {
                    "id": entry_a2.id,
                    "title": "Query A second",
                    "body": "authorized-a-two",
                }
            ],
        }
    ]
    assert {item.item_id for item in traversed.item_provenance} == {
        entry_a1.id,
        entry_a2.id,
    }
    assert entry_b.id not in traversed.model_dump_json()
    assert "forbidden-b-content" not in traversed.model_dump_json()

    first = await execute_query_spec(
        principal_id=user_a.id,
        workspace_id=workspace_a.id,
        spec=QuerySpec(
            resource="entry",
            select=["id", "title", "body"],
            sort=[{"field": "id", "direction": "asc"}],
            limit=1,
        ),
    )
    assert len(first.items) == 1
    assert len(first.item_provenance) == 1
    assert first.next_cursor

    second = await execute_query_spec(
        principal_id=user_a.id,
        workspace_id=workspace_a.id,
        spec=QuerySpec(
            resource="entry",
            select=["id", "title", "body"],
            sort=[{"field": "id", "direction": "asc"}],
            limit=1,
            cursor=first.next_cursor,
        ),
    )
    assert {row["id"] for row in first.items + second.items} == {
        entry_a1.id,
        entry_a2.id,
    }

    forbidden_values = [
        user_b.id,
        workspace_b.id,
        app_b.id,
        track_b.id,
        entry_b.id,
        "Query App B secret",
        "Query Track B secret",
        "Query B secret",
        "forbidden-b-content",
    ]
    for result in (first, second):
        assert len(result.items) == 1
        assert len(result.item_provenance) == 1
        serialized = result.model_dump_json()
        for forbidden in forbidden_values:
            assert forbidden not in serialized
        for metadata in (
            result.graph_revision,
            result.next_cursor or "",
            *(item.model_dump_json() for item in result.item_provenance),
        ):
            for forbidden in forbidden_values:
                assert forbidden not in metadata


@pytest.mark.unit
def test_query_spec_is_declared_as_a_core_read_capability() -> None:
    """The Core snapshot and shared catalogue expose QuerySpec as a read."""
    catalogue = {item["name"]: item for item in build_tool_catalogue()}
    snapshot = {
        item["name"]: item for item in build_core_capability_snapshot()["capabilities"]
    }

    assert catalogue["integral_query_spec"]["op_class"] == "read"
    assert snapshot["integral_query_spec"]["op_class"] == "read"
    spec_schema = catalogue["integral_query_spec"]["input_schema"]["properties"]["spec"]
    assert spec_schema["additionalProperties"] is False
    assert spec_schema["required"] == ["resource", "select"]
    assert spec_schema["properties"]["resource"]["enum"] == ["entry", "track", "app"]


@pytest.mark.asyncio
async def test_query_spec_adapter_receives_broker_execution_identity(
    monkeypatch,
) -> None:
    """The adapter binds provenance identity from the broker invocation."""
    from app.agentive.services.capability_adapters import dispatch_capability
    from app.schemas.capability_broker import CapabilityInvocation

    received = {}

    async def execute(**kwargs):
        received.update(kwargs)
        return QuerySpecResult(
            items=[],
            result_set_id="result-1",
            normalized_plan={"resource": "entry"},
            graph_revision="sha256:revision",
            item_provenance=[],
            redaction_state="none",
        )

    monkeypatch.setattr("app.agentive.services.query_spec.execute_query_spec", execute)
    result = await dispatch_capability(
        CapabilityInvocation(
            run_id="run-1",
            principal_id="user-1",
            workspace_id="workspace-1",
            origin="mcp",
            capability_key="integral_query_spec",
            source="core",
            op_class="read",
            arguments={"spec": {"resource": "entry", "select": ["id"]}},
            idempotency_key="idem-1",
        ),
        {"name": "integral_query_spec", "op_class": "read"},
    )

    assert result["result_set_id"] == "result-1"
    assert received == {
        "principal_id": "user-1",
        "workspace_id": "workspace-1",
        "run_id": "run-1",
        "idempotency_key": "idem-1",
        "spec": QuerySpec(resource="entry", select=["id"]),
    }


@pytest.mark.asyncio
async def test_app_query_snapshot_is_versioned_resolvable_and_fixed(
    monkeypatch,
) -> None:
    """Immutable snapshots retain App query template and package provenance."""
    from app.agentive.nodes import Connector
    from app.agentive.services.capability_broker import resolve_from_snapshot
    from app.schemas.capability_broker import CapabilityInvocation

    app = type(
        "_App",
        (),
        {
            "id": "app-query",
            "installed_package_slug": "query-app",
            "installed_package_version": "2.3.4",
            "source_operational_model_slug": "",
            "version": "",
        },
    )()
    profile = type(
        "_Profile",
        (),
        {
            "id": "profile-query",
            "manifest": {
                "operational_model_schema_version": 2,
                "scope": "app",
                "package": {"slug": "query-app", "version": "2.3.4"},
                "app": {
                    "queries": [
                        {
                            "key": "recent_open",
                            "handler_key": "recent_open",
                            "input_schema": {
                                "type": "object",
                                "properties": {"cursor": {"type": "string"}},
                                "additionalProperties": False,
                            },
                            "output_schema": {"type": "object"},
                            "query_template": {
                                "resource": "entry",
                                "select": ["id", "title"],
                                "filters": [
                                    {
                                        "field": "status",
                                        "op": "eq",
                                        "value": "open",
                                    }
                                ],
                                "limit": 10,
                            },
                        }
                    ]
                },
            },
        },
    )()

    async def find_apps(_query):
        return [app]

    async def find_connectors(_query):
        return []

    async def attached_profile(_app):
        return profile

    monkeypatch.setattr(App, "find", find_apps)
    monkeypatch.setattr(Connector, "find", find_connectors)
    monkeypatch.setattr(
        "app.services.app_graph.get_app_attached_operational_model",
        attached_profile,
    )
    monkeypatch.setattr(
        "app.services.app_operations.registry.list_workspace_operations",
        lambda _workspace_id: {},
    )

    snapshot = await build_capability_snapshot("workspace-query")
    query = snapshot["apps"][0]["queries"][0]
    assert query["query_template"]["resource"] == "entry"
    assert query["package_version"] == "2.3.4"
    assert query["provenance"] == {
        "source": "operational_model",
        "operational_model_id": "profile-query",
        "package_slug": "query-app",
        "package_version": "2.3.4",
    }
    resolved = resolve_from_snapshot(
        snapshot,
        CapabilityInvocation(
            run_id="run-query",
            principal_id="user-query",
            workspace_id="workspace-query",
            origin="chat",
            capability_key="recent_open",
            source="app",
            op_class="read",
            arguments={"cursor": "opaque"},
            app_id="app-query",
        ),
    )
    assert resolved == query


@pytest.mark.asyncio
async def test_app_query_adapter_uses_only_fixed_snapshot_template(
    monkeypatch,
) -> None:
    """App read adapter may add safe cursor input but cannot replace controls."""
    from app.agentive.services.capability_adapters import (
        AdapterError,
        dispatch_capability,
    )
    from app.schemas.capability_broker import CapabilityInvocation

    received = {}

    async def execute(**kwargs):
        received.update(kwargs)
        return QuerySpecResult(
            items=[],
            result_set_id="result-app",
            normalized_plan={"resource": "entry"},
            graph_revision="sha256:revision",
            item_provenance=[],
            redaction_state="none",
        )

    monkeypatch.setattr("app.agentive.services.query_spec.execute_query_spec", execute)
    capability = {
        "key": "recent_open",
        "kind": "read",
        "handler_key": "recent_open",
        "input_schema": {
            "type": "object",
            "properties": {"cursor": {"type": "string"}},
            "additionalProperties": False,
        },
        "query_template": {
            "resource": "entry",
            "select": ["id"],
            "filters": [{"field": "status", "op": "eq", "value": "open"}],
            "sort": [],
            "traversal": [],
            "limit": 10,
            "cost_ceiling": 100,
            "cursor": None,
        },
    }
    base = {
        "run_id": "run-app",
        "principal_id": "user-app",
        "workspace_id": "workspace-app",
        "origin": "chat",
        "capability_key": "recent_open",
        "source": "app",
        "op_class": "read",
        "app_id": "app-query",
        "idempotency_key": "idem-app",
    }

    await dispatch_capability(
        CapabilityInvocation(**base, arguments={"cursor": "opaque"}),
        capability,
    )
    assert received["spec"] == QuerySpec(
        resource="entry",
        select=["id"],
        filters=[{"field": "status", "op": "eq", "value": "open"}],
        limit=10,
        cursor="opaque",
    )

    with pytest.raises(AdapterError, match="query.invalid"):
        await dispatch_capability(
            CapabilityInvocation(
                **base,
                arguments={"resource": "app", "select": ["workspace_id"]},
            ),
            capability,
        )


@pytest.mark.asyncio
async def test_query_spec_http_surface_routes_through_broker(
    authenticated_client, monkeypatch
) -> None:
    """The authenticated HTTP route delegates only to the capability broker."""
    from app.schemas.capability_broker import CapabilityResult

    calls = []

    async def invoke(**kwargs):
        calls.append(kwargs)
        return CapabilityResult(
            ok=True,
            data={
                "items": [{"id": "entry-1"}],
                "replayed": False,
                "result_set_id": "result-1",
                "normalized_plan": {"resource": "entry"},
                "graph_revision": "sha256:revision",
                "item_provenance": [],
                "redaction_state": "none",
                "next_cursor": None,
            },
            receipt={
                "run_id": "run-1",
                "step_key": "capability:idem",
                "idempotency_key": "idem",
                "status": "succeeded",
                "capability_key": "integral_query_spec",
                "origin": "http",
            },
        )

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability",
        invoke,
    )
    response = await authenticated_client.post(
        "/api/query-spec",
        headers={"Idempotency-Key": "idem"},
        json={"resource": "entry", "select": ["id"], "limit": 1},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result_set_id"] == "result-1"
    assert body["receipt"]["run_id"] == "run-1"
    assert calls[0]["capability_key"] == "integral_query_spec"
    assert calls[0]["origin"] == "http"
    assert calls[0]["op_class"] == "read"
    assert calls[0]["arguments"] == {
        "spec": {"resource": "entry", "select": ["id"], "limit": 1}
    }
    assert calls[0]["idempotency_key"] == "idem"


@pytest.mark.asyncio
async def test_query_spec_http_surface_requires_authentication(client) -> None:
    """Anonymous callers cannot reach the QuerySpec surface."""
    response = await client.post(
        "/api/query-spec",
        json={"resource": "entry", "select": ["id"]},
    )

    assert response.status_code in {401, 403}


@pytest.mark.asyncio
async def test_query_spec_http_rejects_malformed_json(authenticated_client) -> None:
    """Malformed request JSON receives the canonical bad-request envelope."""
    response = await authenticated_client.post(
        "/api/query-spec",
        content=b'{"resource":',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400, response.text
    assert response.json()["error_code"] == "bad_request"


@pytest.mark.asyncio
async def test_query_spec_http_idempotency_reuses_surface_run_without_graph_reexecution(
    authenticated_client, monkeypatch
) -> None:
    """A retried HTTP idempotency key replays metadata without graph access."""
    graph_accesses = 0

    async def accessible_entries(_user_id, workspace_id=None, **_kwargs):
        nonlocal graph_accesses
        graph_accesses += 1
        return []

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries",
        accessible_entries,
    )
    request = {
        "headers": {"Idempotency-Key": "same-query-request"},
        "json": {"resource": "entry", "select": ["id"], "limit": 1},
    }

    first = await authenticated_client.post("/api/query-spec", **request)
    second = await authenticated_client.post("/api/query-spec", **request)

    assert first.status_code == second.status_code == 200
    assert graph_accesses == 1
    assert second.json()["items"] is None
    assert second.json()["replayed"] is True
    assert second.json()["result_set_id"] == first.json()["result_set_id"]
    assert second.json()["receipt"]["run_id"] == first.json()["receipt"]["run_id"]
    assert second.json()["receipt"]["step_key"] == first.json()["receipt"]["step_key"]


@pytest.mark.unit
def test_app_query_declaration_compiles_only_a_fixed_bounded_template() -> None:
    """App query declarations compile to closed, bounded descriptors."""
    compiled = compile_canonical_manifest(
        manifest={
            "operational_model_schema_version": 2,
            "scope": "app",
            "package": {"slug": "query-app", "version": "1.0.0"},
            "app": {
                "queries": [
                    {
                        "key": "recent_open",
                        "handler_key": "recent_open",
                        "input_schema": {
                            "type": "object",
                            "properties": {"cursor": {"type": "string"}},
                            "additionalProperties": False,
                        },
                        "output_schema": {"type": "object"},
                        "query_template": {
                            "resource": "entry",
                            "select": ["id", "title"],
                            "filters": [
                                {"field": "status", "op": "eq", "value": "open"}
                            ],
                            "sort": [{"field": "updated_at", "direction": "desc"}],
                            "limit": 20,
                            "cost_ceiling": 100,
                        },
                    }
                ]
            },
        }
    )

    assert compiled["app"]["queries"] == [
        {
            "key": "recent_open",
            "handler_key": "recent_open",
            "input_schema": {
                "type": "object",
                "properties": {"cursor": {"type": "string"}},
                "additionalProperties": False,
            },
            "output_schema": {"type": "object"},
            "query_template": {
                "resource": "entry",
                "select": ["id", "title"],
                "filters": [{"field": "status", "op": "eq", "value": "open"}],
                "sort": [{"field": "updated_at", "direction": "desc"}],
                "traversal": [],
                "limit": 20,
                "cost_ceiling": 100,
                "cursor": None,
            },
        }
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "caller_controlled", ["resource", "filters", "traversal", "select"]
)
def test_app_query_declaration_rejects_open_query_spec_inputs(
    caller_controlled: str,
) -> None:
    """App inputs cannot expose the open Core QuerySpec controls."""
    with pytest.raises(OperationalModelValidationError, match=caller_controlled):
        compile_canonical_manifest(
            manifest={
                "operational_model_schema_version": 2,
                "scope": "app",
                "package": {"slug": "query-app", "version": "1.0.0"},
                "app": {
                    "queries": [
                        {
                            "key": "unsafe",
                            "handler_key": "unsafe",
                            "input_schema": {
                                "type": "object",
                                "properties": {caller_controlled: {"type": "object"}},
                            },
                            "output_schema": {"type": "object"},
                            "query_template": {
                                "resource": "entry",
                                "select": ["id"],
                                "limit": 10,
                            },
                        }
                    ]
                },
            }
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "input_schema",
    [
        {
            "type": "object",
            "properties": {
                "safe": {
                    "type": "object",
                    "properties": {"filters": {"type": "array"}},
                }
            },
        },
        {"type": "object", "patternProperties": {".*": {"type": "string"}}},
        {"type": "object", "additionalProperties": {"type": "string"}},
        {
            "type": "object",
            "properties": {
                "safe": {
                    "oneOf": [
                        {
                            "type": "object",
                            "properties": {"select": {"type": "array"}},
                        }
                    ]
                }
            },
        },
    ],
)
def test_app_query_declaration_recursively_closes_input_schema(
    input_schema: dict[str, Any],
) -> None:
    """Nested and wildcard schema constructs cannot reopen QuerySpec controls."""
    with pytest.raises(OperationalModelValidationError):
        compile_canonical_manifest(
            manifest={
                "operational_model_schema_version": 2,
                "scope": "app",
                "package": {"slug": "query-app", "version": "1.0.0"},
                "app": {
                    "queries": [
                        {
                            "key": "unsafe",
                            "handler_key": "unsafe",
                            "input_schema": input_schema,
                            "output_schema": {"type": "object"},
                            "query_template": {
                                "resource": "entry",
                                "select": ["id"],
                            },
                        }
                    ]
                },
            }
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "query_template",
    [
        {"resource": "entry", "select": ["password_hash"]},
        {
            "resource": "entry",
            "select": ["id"],
            "filters": [{"field": "unknown", "op": "eq", "value": 1}],
        },
        {
            "resource": "entry",
            "select": ["id"],
            "traversal": [{"edge": "app", "select": ["id"]}],
        },
    ],
)
def test_app_query_declaration_rejects_semantically_unsupported_template(
    query_template: dict[str, Any],
) -> None:
    """Unsupported fixed fields and edges fail during manifest compilation."""
    with pytest.raises(OperationalModelValidationError, match="query_template"):
        compile_canonical_manifest(
            manifest={
                "operational_model_schema_version": 2,
                "scope": "app",
                "package": {"slug": "query-app", "version": "1.0.0"},
                "app": {
                    "queries": [
                        {
                            "key": "unsupported",
                            "handler_key": "unsupported",
                            "input_schema": {
                                "type": "object",
                                "properties": {},
                                "additionalProperties": False,
                            },
                            "output_schema": {"type": "object"},
                            "query_template": query_template,
                        }
                    ]
                },
            }
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("schema_key", "schema"),
    [
        ("input_schema", {"type": "object", "properties": {"cursor": {"type": 7}}}),
        ("output_schema", {"type": "object", "required": "not-a-list"}),
    ],
)
def test_app_query_declaration_rejects_invalid_json_schema(
    schema_key: str,
    schema: dict[str, Any],
) -> None:
    """Malformed App query input and output schemas fail at compile time."""
    descriptor = {
        "key": "invalid-schema",
        "handler_key": "invalid-schema",
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "output_schema": {"type": "object"},
        "query_template": {"resource": "entry", "select": ["id"]},
    }
    descriptor[schema_key] = schema

    with pytest.raises(OperationalModelValidationError, match=schema_key):
        compile_canonical_manifest(
            manifest={
                "operational_model_schema_version": 2,
                "scope": "app",
                "package": {"slug": "query-app", "version": "1.0.0"},
                "app": {"queries": [descriptor]},
            }
        )


@pytest.mark.unit
def test_app_manifest_rejects_operation_query_key_collision() -> None:
    """Operation and query namespaces cannot classify one key differently."""
    with pytest.raises(OperationalModelValidationError, match="duplicate.*shared"):
        compile_canonical_manifest(
            manifest={
                "operational_model_schema_version": 2,
                "scope": "app",
                "package": {"slug": "query-app", "version": "1.0.0"},
                "app": {
                    "operations": [
                        {
                            "key": "shared",
                            "kind": "execute",
                            "handler_ref": "tools:run",
                        }
                    ],
                    "queries": [
                        {
                            "key": "shared",
                            "handler_key": "shared",
                            "input_schema": {
                                "type": "object",
                                "properties": {},
                                "additionalProperties": False,
                            },
                            "output_schema": {"type": "object"},
                            "query_template": {
                                "resource": "entry",
                                "select": ["id"],
                            },
                        }
                    ],
                },
            }
        )


@pytest.mark.asyncio
async def test_app_extension_route_invokes_declared_query_as_broker_read(
    authenticated_client, monkeypatch
) -> None:
    """The App extension POST surface dispatches query declarations as reads."""
    from app.schemas.capability_broker import CapabilityResult

    calls = []

    async def snapshot(_workspace_id):
        return {
            "apps": [
                {
                    "app_id": "app-query",
                    "queries": [{"key": "recent_open", "kind": "read"}],
                }
            ]
        }

    async def invoke(**kwargs):
        calls.append(kwargs)
        return CapabilityResult(
            ok=True,
            data={
                "items": [{"id": "entry-1"}],
                "replayed": False,
                "result_set_id": "result-app",
                "normalized_plan": {"resource": "entry"},
                "graph_revision": "sha256:revision",
                "item_provenance": [],
                "redaction_state": "none",
            },
            receipt={
                "run_id": "run-app",
                "step_key": "capability:query",
                "idempotency_key": "idem-app",
                "status": "succeeded",
                "capability_key": "recent_open",
                "origin": "view",
            },
        )

    monkeypatch.setattr(
        "app.agentive.services.execution_runs.build_capability_snapshot",
        snapshot,
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability",
        invoke,
    )
    response = await authenticated_client.post(
        "/api/extensions/app-query/operations/recent_open",
        headers={"X-Integral-Run-Origin": "view", "Idempotency-Key": "idem-app"},
        json={"input": {"cursor": "opaque"}},
    )

    assert response.status_code == 200, response.text
    assert calls[0]["source"] == "app"
    assert calls[0]["op_class"] == "read"
    assert calls[0]["arguments"] == {"cursor": "opaque"}
    assert response.json()["output"]["result_set_id"] == "result-app"
    assert response.json()["receipt"]["capability_key"] == "recent_open"


@pytest.mark.asyncio
async def test_app_extension_route_exposes_non_query_replay_marker(
    authenticated_client, monkeypatch
) -> None:
    """HTTP callers can distinguish safe replay from a fresh empty success."""
    from app.schemas.capability_broker import CapabilityResult

    async def snapshot(_workspace_id):
        return {
            "apps": [
                {
                    "app_id": "app-operation",
                    "operations": [{"key": "send_once", "kind": "execute"}],
                    "queries": [],
                }
            ]
        }

    async def invoke(**_kwargs):
        return CapabilityResult(
            ok=True,
            data={"replayed": True, "result_unavailable": True},
            message=(
                "Prior result payload is not retained; "
                "capability was not executed again."
            ),
            replayed=True,
            receipt={
                "run_id": "run-replay",
                "step_key": "capability:send-once",
                "idempotency_key": "idem-replay",
                "status": "succeeded",
                "capability_key": "send_once",
                "origin": "http",
            },
        )

    monkeypatch.setattr(
        "app.agentive.services.execution_runs.build_capability_snapshot",
        snapshot,
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability",
        invoke,
    )
    response = await authenticated_client.post(
        "/api/extensions/app-operation/operations/send_once",
        headers={"Idempotency-Key": "idem-replay"},
        json={"input": {"private": "must-not-be-replayed"}},
    )

    assert response.status_code == 200, response.text
    assert response.json()["output"] == {
        "replayed": True,
        "result_unavailable": True,
    }
    assert "must-not-be-replayed" not in response.text


@pytest.mark.asyncio
async def test_app_extension_listing_exposes_declared_query_descriptors(
    authenticated_client, monkeypatch
) -> None:
    """The extension contract lists App queries separately from operations."""

    async def operations(**_kwargs):
        return {"app_id": "app-query", "operations": []}

    async def snapshot(_workspace_id):
        return {
            "apps": [
                {
                    "app_id": "app-query",
                    "queries": [
                        {
                            "key": "recent_open",
                            "kind": "read",
                            "handler_key": "recent_open",
                            "input_schema": {
                                "type": "object",
                                "properties": {},
                                "additionalProperties": False,
                            },
                            "output_schema": {"type": "object"},
                            "provenance": {
                                "source": "operational_model",
                                "package_version": "1.0.0",
                            },
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr(
        "app.services.app_operations.dispatch.list_app_operations",
        operations,
    )
    monkeypatch.setattr(
        "app.agentive.services.execution_runs.build_capability_snapshot",
        snapshot,
    )
    response = await authenticated_client.get("/api/extensions/app-query/operations")

    assert response.status_code == 200, response.text
    assert response.json()["operations"] == []
    assert response.json()["queries"][0]["key"] == "recent_open"
    assert response.json()["queries"][0]["kind"] == "read"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_query_spec_debug_metadata_contains_only_provenance_identifiers() -> None:
    """Query rows and values never leak into the final debug envelope."""
    from app.providers.jvagent_streaming import (
        fresh_translator_state,
        translate_envelope,
    )

    state = fresh_translator_state(started=0.0, run_id="query-run-1")

    async def translate(parsed):
        return [event async for event in translate_envelope(parsed, state)]

    await translate(
        {
            "type": "message",
            "message": {
                "category": "thought",
                "thought_type": "tool_result",
                "segment_id": "query-segment",
                "metadata": {
                    "tool_name": "integral_query_spec",
                    "tool_result": {
                        "items": [
                            {
                                "id": "entry-private",
                                "title": "private result title",
                            }
                        ],
                        "result_set_id": "result-set-1",
                        "normalized_plan": {
                            "filters": [
                                {
                                    "field": "title",
                                    "op": "eq",
                                    "value": "private query value",
                                }
                            ]
                        },
                        "graph_revision": "sha256:graph-revision",
                        "item_provenance": [
                            {
                                "item_id": "entry-private",
                                "resource": "entry",
                                "fingerprint": "sha256:item-fingerprint",
                            }
                        ],
                        "_receipt": {
                            "run_id": "query-run-1",
                            "step_key": "capability:query-step",
                            "idempotency_key": "idem-query",
                            "status": "succeeded",
                            "capability_key": "integral_query_spec",
                            "origin": "chat",
                            "snapshot_fingerprint": "sha256:snapshot",
                        },
                    },
                },
            },
        }
    )
    await translate(
        {
            "type": "message",
            "message": {
                "category": "thought",
                "thought_type": "tool_result",
                "segment_id": "page-context-segment",
                "metadata": {
                    "tool_name": "integral_get_page_context",
                    "tool_result": {
                        "result_set_id": "spoofed-page-result-set",
                        "page_context": {"title": "private page title"},
                    },
                },
            },
        }
    )
    final_events = await translate(
        {"type": "final", "interaction": {"response": "Done"}}
    )
    final_payload = next(
        event["payload"] for event in final_events if event["type"] == "final-content"
    )
    provenance = final_payload["claim_provenance"]

    assert provenance["tools"] == [
        {
            "name": "integral_query_spec",
            "source": "query",
            "status": "complete",
            "result_set_id": "result-set-1",
            "run_id": "query-run-1",
            "receipt": {
                "run_id": "query-run-1",
                "step_key": "capability:query-step",
                "status": "succeeded",
                "capability_key": "integral_query_spec",
                "origin": "chat",
            },
            "graph_revision": "sha256:graph-revision",
        },
        {
            "name": "integral_get_page_context",
            "source": "page_context",
            "status": "complete",
        },
    ]
    serialized = json.dumps(provenance)
    assert "private result title" not in serialized
    assert "private query value" not in serialized
    assert "spoofed-page-result-set" not in serialized
    assert "private page title" not in serialized
    assert "entry-private" not in serialized
    assert "sha256:item-fingerprint" not in serialized
    assert "idem-query" not in serialized
    assert "sha256:snapshot" not in serialized


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "tool_args", "receipt"),
    [
        (
            "integral_get_page_context",
            {},
            {
                "run_id": "run-1",
                "status": "succeeded",
                "capability_key": "integral_query_spec",
            },
        ),
        (
            "integral_list_tracks",
            {},
            {
                "run_id": "run-1",
                "status": "succeeded",
                "capability_key": "integral_query_spec",
            },
        ),
        (
            "integral_query_spec",
            {},
            {
                "run_id": "run-other",
                "status": "succeeded",
                "capability_key": "integral_query_spec",
            },
        ),
        (
            "integral_query_spec",
            {},
            {
                "run_id": "run-1",
                "status": "failed",
                "capability_key": "integral_query_spec",
            },
        ),
        (
            "integral_query_spec",
            {},
            {
                "run_id": "run-1",
                "status": "succeeded",
                "capability_key": "recent_open",
            },
        ),
        (
            "integral_invoke_app_operation",
            {"operation_key": "recent_open"},
            {
                "run_id": "run-1",
                "status": "succeeded",
                "capability_key": "different_query",
            },
        ),
    ],
)
async def test_query_debug_rejects_spoofed_or_inconsistent_result_sets(
    tool_name: str, tool_args: dict[str, Any], receipt: dict[str, str]
) -> None:
    """Only broker-consistent QuerySpec receipts may label result sets."""
    from app.providers.jvagent_streaming import (
        fresh_translator_state,
        translate_envelope,
    )

    state = fresh_translator_state(started=0.0, run_id="run-1")
    result = {
        "result_set_id": "spoofed-result-set",
        "items": [{"id": "private-row"}],
        "_receipt": receipt,
    }
    _ = [
        event
        async for event in translate_envelope(
            {
                "type": "message",
                "message": {
                    "category": "thought",
                    "thought_type": "tool_result",
                    "metadata": {
                        "tool_name": tool_name,
                        "tool_args": tool_args,
                        "tool_result": result,
                    },
                },
            },
            state,
        )
    ]
    final = [
        event
        async for event in translate_envelope(
            {"type": "final", "interaction": {"response": "Done"}},
            state,
        )
    ]
    provenance = next(
        event["payload"]["claim_provenance"]
        for event in final
        if event["type"] == "final-content"
    )

    assert "result_set_id" not in json.dumps(provenance)
    assert "private-row" not in json.dumps(provenance)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generic_app_query_debug_requires_matching_receipt_capability() -> None:
    """Generic App dispatch trusts only its reclassified query receipt."""
    from app.providers.jvagent_streaming import (
        fresh_translator_state,
        translate_envelope,
    )

    state = fresh_translator_state(started=0.0, run_id="run-1")
    _ = [
        event
        async for event in translate_envelope(
            {
                "type": "message",
                "message": {
                    "category": "thought",
                    "thought_type": "tool_result",
                    "metadata": {
                        "tool_name": "integral_invoke_app_operation",
                        "tool_args": {
                            "app_id": "app-1",
                            "operation_key": "recent_open",
                            "input": {"secret_filter": "private query value"},
                        },
                        "tool_result": {
                            "result_set_id": "app-result-set",
                            "items": [{"id": "private-row"}],
                            "graph_revision": "sha256:app-revision",
                            "_receipt": {
                                "run_id": "run-1",
                                "step_key": "capability:recent-open",
                                "status": "succeeded",
                                "capability_key": "recent_open",
                                "origin": "chat",
                            },
                        },
                    },
                },
            },
            state,
        )
    ]
    final = [
        event
        async for event in translate_envelope(
            {"type": "final", "interaction": {"response": "Done"}},
            state,
        )
    ]
    provenance = next(
        event["payload"]["claim_provenance"]
        for event in final
        if event["type"] == "final-content"
    )

    assert provenance["tools"][0]["result_set_id"] == "app-result-set"
    assert provenance["tools"][0]["run_id"] == "run-1"
    serialized = json.dumps(provenance)
    assert "private-row" not in serialized
    assert "private query value" not in serialized
