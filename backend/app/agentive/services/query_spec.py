"""Bounded, workspace-authorized QuerySpec compilation and execution."""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from numbers import Real
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.contracts.information import resolve_legacy_entry_field_path_value
from app.models.edges import ANCHORS, CONTAINS, REFERENCES
from app.models.query_result_set import QueryResultSet
from app.schemas.query_spec import (
    QUERY_RESOURCE_FIELDS,
    QueryItemProvenance,
    QuerySpec,
    QuerySpecResult,
    is_allowed_query_field,
    validate_query_spec_semantics,
)
from app.services.permissions import (
    get_user_accessible_apps,
    get_user_accessible_entries,
    get_user_accessible_tracks,
)


class QuerySpecError(ValueError):
    """A deterministic rejection of an invalid or over-budget query plan."""


QuerySpecExecutionError = QuerySpecError

MAX_AUTHORIZED_SCAN = 1000


RESOURCE_FIELDS = QUERY_RESOURCE_FIELDS

# edge key: target resource, edge class, direction, target entity, edge entity
RESOURCE_EDGES: Dict[str, Dict[str, Tuple[str, Any, str, str, str]]] = {
    "entry": {
        "track": ("track", CONTAINS, "in", "Track", "CONTAINS"),
        "references": ("entry", REFERENCES, "out", "Entry", "REFERENCES"),
        "anchored_tracks": ("track", ANCHORS, "out", "Track", "Anchors"),
    },
    "track": {
        "app": ("app", CONTAINS, "in", "WorkspaceApp", "CONTAINS"),
        "entries": ("entry", CONTAINS, "out", "Entry", "CONTAINS"),
    },
    "app": {
        "tracks": ("track", CONTAINS, "out", "Track", "CONTAINS"),
    },
}

SUPPORTED_OPERATORS = frozenset(
    {"eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "is_null"}
)


async def execute_query_spec(
    *,
    principal_id: str,
    workspace_id: str,
    spec: QuerySpec,
    run_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> QuerySpecResult:
    """Validate and execute a one-hop query over workspace-authorized roots."""

    try:
        validate_query_spec_semantics(spec)
    except ValueError as exc:
        raise QuerySpecExecutionError(str(exc)) from exc
    for field_name in spec.select:
        if not is_allowed_query_field(spec.resource, field_name):
            raise QuerySpecExecutionError(
                f"field '{field_name}' is not allowed for {spec.resource}"
            )
    for query_filter in spec.filters:
        if not is_allowed_query_field(spec.resource, query_filter.field):
            raise QuerySpecExecutionError(
                f"field '{query_filter.field}' is not allowed for {spec.resource}"
            )
        if query_filter.op not in SUPPORTED_OPERATORS:
            raise QuerySpecError(f"operator '{query_filter.op}' is not allowed")
        if query_filter.op in {"in", "not_in"} and not isinstance(
            query_filter.value, (list, tuple, set, frozenset)
        ):
            raise QuerySpecError(
                f"'{query_filter.op}' filter value must be a collection"
            )
        if query_filter.op == "is_null" and not isinstance(query_filter.value, bool):
            raise QuerySpecError("'is_null' filter value must be a boolean")
    for query_sort in spec.sort:
        if not is_allowed_query_field(spec.resource, query_sort.field):
            raise QuerySpecExecutionError(
                f"field '{query_sort.field}' is not allowed for {spec.resource}"
            )
    for traversal in spec.traversal:
        edge = RESOURCE_EDGES[spec.resource].get(traversal.edge)
        if edge is None:
            raise QuerySpecError(
                f"edge '{traversal.edge}' is not allowed for {spec.resource}"
            )
        target_resource, _, fixed_direction, _, _ = edge
        if traversal.direction != fixed_direction:
            raise QuerySpecError(
                f"direction '{traversal.direction}' is not allowed for edge "
                f"'{traversal.edge}'; expected '{fixed_direction}'"
            )
        for field_name in traversal.select:
            if not is_allowed_query_field(target_resource, field_name):
                raise QuerySpecError(
                    f"field '{field_name}' is not allowed for {target_resource}"
                )

    base_work_per_candidate = (
        len(spec.select) + (2 * len(spec.filters)) + (2 * len(spec.sort))
    )
    estimated_cost = spec.limit * base_work_per_candidate + spec.limit * sum(
        traversal.limit for traversal in spec.traversal
    )
    if estimated_cost > spec.cost_ceiling:
        raise QuerySpecError(
            f"estimated cost {estimated_cost} exceeds cost ceiling "
            f"{spec.cost_ceiling}"
        )

    canonical_plan = spec.model_dump(mode="json", exclude={"cursor"})
    canonical_plan["cost_model"] = {
        "formula": (
            "authorized_root_count * base_units + page_root_count * "
            "sum(authorized_target_universe_count)"
        ),
        "actual_formula": "authorized_root_count * base_units + edge_scans",
        "base_units": base_work_per_candidate,
        "static_estimate": estimated_cost,
    }
    fingerprint_source = json.dumps(
        {
            "canonical_plan": canonical_plan,
            "principal_id": principal_id,
            "workspace_id": workspace_id,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    plan_fingerprint = (
        "hmac-sha256:"
        + hmac.new(
            settings.SECRET_KEY.encode("utf-8"),
            fingerprint_source.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    )
    normalized_plan = copy.deepcopy(canonical_plan)
    normalized_plan["filters"] = []
    for query_filter in spec.filters:
        value = query_filter.value
        if value is None:
            value_type = "null"
        elif isinstance(value, bool):
            value_type = "boolean"
        elif isinstance(value, Real):
            value_type = "number"
        elif isinstance(value, str):
            value_type = "string"
        elif isinstance(value, list):
            value_type = "array"
        else:
            value_type = "object"
        encoded_value = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        normalized_plan["filters"].append(
            {
                "field": query_filter.field,
                "op": query_filter.op,
                "value_type": value_type,
                "value_fingerprint": (
                    "hmac-sha256:"
                    + hmac.new(
                        settings.SECRET_KEY.encode("utf-8"),
                        encoded_value,
                        hashlib.sha256,
                    ).hexdigest()
                ),
                "redacted": True,
            }
        )

    expired_result_sets: List[QueryResultSet] = []
    expired_predecessor: Optional[QueryResultSet] = None
    if run_id and idempotency_key:
        result_sets: List[QueryResultSet] = list(
            await QueryResultSet.find(
                {
                    "context.run_id": run_id,
                    "context.idempotency_key": idempotency_key,
                    "context.principal_id": principal_id,
                    "context.workspace_id": workspace_id,
                }
            )
        )  # type: ignore[assignment]
        active_result_sets: List[QueryResultSet] = []
        replay_now = datetime.now(timezone.utc)
        for result_set in result_sets:
            try:
                expires_at = datetime.fromisoformat(
                    result_set.expires_at.replace("Z", "+00:00")
                )
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                expired = expires_at <= replay_now
            except (AttributeError, TypeError, ValueError):
                expired = True
            if expired:
                expired_result_sets.append(result_set)
            else:
                active_result_sets.append(result_set)

        if active_result_sets:
            existing_result_set = max(
                active_result_sets,
                key=lambda item: (
                    item.created_at,
                    item.expires_at,
                    item.result_set_id,
                ),
            )
            if existing_result_set.plan_fingerprint != plan_fingerprint:
                raise QuerySpecError(
                    "query.idempotency_conflict: idempotency key reused "
                    "with a different query plan"
                )
            for expired_result_set in expired_result_sets:
                await expired_result_set.delete()
            return QuerySpecResult(
                items=None,
                replayed=True,
                result_set_id=existing_result_set.result_set_id,
                normalized_plan=existing_result_set.normalized_plan,
                graph_revision=existing_result_set.graph_revision,
                item_provenance=[
                    QueryItemProvenance.model_validate(item)
                    for item in existing_result_set.item_provenance
                ],
                redaction_state=existing_result_set.redaction_state,
            )

        if expired_result_sets:
            expired_predecessor = max(
                expired_result_sets,
                key=lambda item: (
                    item.expires_at,
                    item.created_at,
                    item.result_set_id,
                ),
            )
            # Remove stale receipts before recomputing. This lets the durable
            # create-if-absent claim below decide the single replacement.
            for expired_result_set in expired_result_sets:
                await expired_result_set.delete()

    def native_sort_key(value: Any) -> Tuple[int, Any]:
        if isinstance(value, bool):
            return (0, value)
        if isinstance(value, Real):
            return (1, value)
        if isinstance(value, datetime):
            return (2, value)
        if isinstance(value, date):
            return (3, value)
        if isinstance(value, str):
            return (4, value)
        if value is None:
            return (6, "")
        return (
            5,
            json.dumps(value, sort_keys=True, default=str, separators=(",", ":")),
        )

    def field_value(resource: str, item: Any, field_name: str) -> Any:
        if resource != "entry":
            return getattr(item, field_name, None)
        if field_name.startswith("custom_fields"):
            raw_entry = {
                key: getattr(item, key, None)
                for key in (
                    "id",
                    "title",
                    "body",
                    "status",
                    "type_id",
                    "author_id",
                    "track_id",
                    "created_at",
                    "updated_at",
                )
            }
            raw_entry["custom_fields"] = getattr(item, "custom_fields", None)
            return resolve_legacy_entry_field_path_value(field_name, raw_entry)
        return getattr(item, field_name, None)

    def encode_sort_value(value: Any) -> List[Any]:
        rank, native_value = native_sort_key(value)
        if rank in {2, 3}:
            native_value = native_value.isoformat()
        return [rank, native_value]

    def decode_sort_value(value: Any) -> Tuple[int, Any]:
        if (
            not isinstance(value, list)
            or len(value) != 2
            or not isinstance(value[0], int)
            or isinstance(value[0], bool)
            or value[0] not in range(7)
        ):
            raise QuerySpecError("invalid query cursor")
        rank, native_value = value
        try:
            if rank == 2:
                native_value = datetime.fromisoformat(native_value)
            elif rank == 3:
                native_value = date.fromisoformat(native_value)
        except (TypeError, ValueError) as exc:
            raise QuerySpecError("invalid query cursor") from exc
        return (rank, native_value)

    cursor_payload = None
    cursor_last_sort: List[Tuple[int, Any]] = []
    if spec.cursor:
        try:
            padded = spec.cursor + ("=" * (-len(spec.cursor) % 4))
            cursor_envelope = json.loads(
                base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
            )
            if (
                not isinstance(cursor_envelope, dict)
                or not isinstance(cursor_envelope.get("payload"), dict)
                or not isinstance(cursor_envelope.get("signature"), str)
            ):
                raise QuerySpecError("invalid query cursor")
            cursor_payload = cursor_envelope["payload"]
            signed_bytes = json.dumps(
                cursor_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
            expected_signature = hmac.new(
                settings.SECRET_KEY.encode("utf-8"),
                signed_bytes,
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(
                cursor_envelope["signature"], expected_signature
            ):
                raise QuerySpecError("invalid query cursor signature")
            if cursor_payload.get("fingerprint") != plan_fingerprint:
                raise QuerySpecError("cursor does not match the normalized query plan")
            if (
                not isinstance(cursor_payload.get("last_sort"), list)
                or len(cursor_payload["last_sort"]) != len(spec.sort)
                or not isinstance(cursor_payload.get("id"), str)
            ):
                raise QuerySpecError("invalid query cursor")
            cursor_last_sort = [
                decode_sort_value(value) for value in cursor_payload["last_sort"]
            ]
        except QuerySpecError:
            raise
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise QuerySpecError("invalid query cursor") from exc

    async def authorized_items(resource: str) -> List[Any]:
        if resource == "entry":
            items = list(
                await get_user_accessible_entries(
                    principal_id, workspace_id=workspace_id
                )
            )
        elif resource == "track":
            items = [
                item
                for item in await get_user_accessible_tracks(principal_id)
                if str(getattr(item, "workspace_id", "")) == workspace_id
            ]
        else:
            items = [
                item
                for item in await get_user_accessible_apps(principal_id)
                if str(getattr(item, "workspace_id", "")) == workspace_id
            ]
        if len(items) > MAX_AUTHORIZED_SCAN:
            raise QuerySpecError(
                f"authorized {resource} scan {len(items)} exceeds limit "
                f"{MAX_AUTHORIZED_SCAN}"
            )
        return items

    roots = await authorized_items(spec.resource)
    dynamic_base_cost = len(roots) * base_work_per_candidate
    if dynamic_base_cost > spec.cost_ceiling:
        raise QuerySpecError(
            f"dynamic estimated cost {dynamic_base_cost} exceeds cost ceiling "
            f"{spec.cost_ceiling}"
        )

    def matches_filters(item: Any) -> bool:
        for query_filter in spec.filters:
            actual = field_value(spec.resource, item, query_filter.field)
            expected = query_filter.value
            if query_filter.op == "eq" and actual != expected:
                return False
            if query_filter.op == "ne" and actual == expected:
                return False
            if query_filter.op == "gt":
                try:
                    if actual <= expected:
                        return False
                except TypeError:
                    return False
            if query_filter.op == "gte":
                try:
                    if actual < expected:
                        return False
                except TypeError:
                    return False
            if query_filter.op == "lt":
                try:
                    if actual >= expected:
                        return False
                except TypeError:
                    return False
            if query_filter.op == "lte":
                try:
                    if actual > expected:
                        return False
                except TypeError:
                    return False
            if query_filter.op == "in" and actual not in expected:
                return False
            if query_filter.op == "not_in" and actual in expected:
                return False
            if query_filter.op == "contains":
                try:
                    if expected not in actual:
                        return False
                except TypeError:
                    return False
            if query_filter.op == "is_null" and (
                (actual is None) != query_filter.value
            ):
                return False
        return True

    roots = [item for item in roots if matches_filters(item)]

    # Seed a deterministic tie-breaker before applying the user sort keys.
    roots.sort(key=lambda item: str(getattr(item, "id", "")))

    for query_sort in reversed(spec.sort):
        non_null = [
            item
            for item in roots
            if field_value(spec.resource, item, query_sort.field) is not None
        ]
        nulls = [
            item
            for item in roots
            if field_value(spec.resource, item, query_sort.field) is None
        ]
        sort_field = query_sort.field
        non_null.sort(
            key=lambda item: native_sort_key(
                field_value(spec.resource, item, sort_field)
            ),
            reverse=query_sort.direction == "desc",
        )
        roots = non_null + nulls

    if cursor_payload is not None:

        def is_after_cursor(item: Any) -> bool:
            for index, query_sort in enumerate(spec.sort):
                item_key = native_sort_key(
                    field_value(spec.resource, item, query_sort.field)
                )
                cursor_key = cursor_last_sort[index]
                if item_key == cursor_key:
                    continue
                if item_key[0] == 6 or cursor_key[0] == 6:
                    return item_key > cursor_key
                if query_sort.direction == "desc":
                    return item_key < cursor_key
                return item_key > cursor_key
            return str(getattr(item, "id", "")) > cursor_payload["id"]

        roots = [item for item in roots if is_after_cursor(item)]

    page = roots[: spec.limit]
    next_cursor = None
    if len(roots) > spec.limit:
        last_item = page[-1]
        next_payload = {
            "fingerprint": plan_fingerprint,
            "last_sort": [
                encode_sort_value(
                    field_value(spec.resource, last_item, query_sort.field)
                )
                for query_sort in spec.sort
            ],
            "id": str(last_item.id),
        }
        signed_bytes = json.dumps(
            next_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        cursor_json = json.dumps(
            {
                "payload": next_payload,
                "signature": hmac.new(
                    settings.SECRET_KEY.encode("utf-8"),
                    signed_bytes,
                    hashlib.sha256,
                ).hexdigest(),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        next_cursor = (
            base64.urlsafe_b64encode(cursor_json.encode("utf-8"))
            .decode("ascii")
            .rstrip("=")
        )

    target_sets: Dict[str, Dict[str, Any]] = {}
    traversal_target_work = 0
    for traversal in spec.traversal:
        target_resource = RESOURCE_EDGES[spec.resource][traversal.edge][0]
        if target_resource not in target_sets:
            target_sets[target_resource] = {
                str(candidate.id): candidate
                for candidate in await authorized_items(target_resource)
            }
        traversal_target_work += len(target_sets[target_resource])
    dynamic_cost = dynamic_base_cost + (len(page) * traversal_target_work)
    if dynamic_cost > spec.cost_ceiling:
        raise QuerySpecError(
            f"dynamic estimated cost {dynamic_cost} exceeds cost ceiling "
            f"{spec.cost_ceiling}"
        )

    rows: List[Dict[str, Any]] = []
    revision_items: Dict[Tuple[str, str], Any] = {}
    edge_scan_count = 0
    for item in page:
        row = {
            field_name: field_value(spec.resource, item, field_name)
            for field_name in spec.select
        }
        revision_items[(spec.resource, str(item.id))] = item
        for traversal in spec.traversal:
            (
                target_resource,
                edge_class,
                _,
                _,
                edge_entity,
            ) = RESOURCE_EDGES[
                spec.resource
            ][traversal.edge]
            authorized_target_ids = list(target_sets[target_resource])
            if traversal.direction == "out":
                edge_query = {
                    "entity": edge_entity,
                    "source": str(item.id),
                    "target": {"$in": authorized_target_ids},
                }
                target_endpoint = "target"
            else:
                edge_query = {
                    "entity": edge_entity,
                    "source": {"$in": authorized_target_ids},
                    "target": str(item.id),
                }
                target_endpoint = "source"

            authorized_target_ids_found = set()
            traversed_edge_count = 0
            context = await item.get_context()
            async for graph_edge in context.async_edge_iterator(edge_class, edge_query):
                target_id = str(getattr(graph_edge, target_endpoint))
                if target_id not in target_sets[target_resource]:
                    continue
                traversed_edge_count += 1
                edge_scan_count += 1
                actual_cost = dynamic_base_cost + edge_scan_count
                if actual_cost > spec.cost_ceiling:
                    raise QuerySpecError(
                        f"query.cost_exceeded: actual cost {actual_cost} exceeds "
                        f"cost ceiling {spec.cost_ceiling}"
                    )
                if traversed_edge_count > MAX_AUTHORIZED_SCAN:
                    break
                authorized_target_ids_found.add(target_id)
            if traversed_edge_count > MAX_AUTHORIZED_SCAN:
                raise QuerySpecError(
                    "authorized traversal edge scan exceeds limit "
                    f"{MAX_AUTHORIZED_SCAN}"
                )
            authorized = [
                target_sets[target_resource][target_id]
                for target_id in sorted(authorized_target_ids_found)[: traversal.limit]
            ]
            row[traversal.edge] = [
                {
                    field_name: field_value(target_resource, candidate, field_name)
                    for field_name in traversal.select
                }
                for candidate in authorized
            ]
            for candidate in authorized:
                revision_items[(target_resource, str(candidate.id))] = candidate
        rows.append(row)

    def item_fingerprint(resource: str, item: Any) -> str:
        identity = json.dumps(
            {
                "id": str(item.id),
                "resource": resource,
                "updated_at": str(getattr(item, "updated_at", "") or ""),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return "sha256:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()

    provenance = [
        QueryItemProvenance(
            item_id=str(item.id),
            resource=resource,
            fingerprint=item_fingerprint(resource, item),
        )
        for (resource, _), item in sorted(revision_items.items())
    ]
    revision_source = "|".join(
        item_fingerprint(resource, item)
        for (resource, _), item in sorted(revision_items.items())
    )
    graph_revision = (
        "sha256:" + hashlib.sha256(revision_source.encode("utf-8")).hexdigest()
    )
    normalized_plan["cost_model"]["edge_scans"] = edge_scan_count
    normalized_plan["cost_model"]["actual_cost"] = dynamic_base_cost + edge_scan_count

    result_set_id = str(uuid.uuid4())
    item_provenance = [item.model_dump(mode="json") for item in provenance]
    now = datetime.now(timezone.utc)
    metadata = {
        "result_set_id": result_set_id,
        "run_id": run_id or "",
        "principal_id": principal_id,
        "workspace_id": workspace_id,
        "idempotency_key": idempotency_key or "",
        "plan_fingerprint": plan_fingerprint,
        "normalized_plan": normalized_plan,
        "graph_revision": graph_revision,
        "item_provenance": item_provenance,
        "item_fingerprints": [item["fingerprint"] for item in item_provenance],
        "redaction_state": "none",
        "expires_at": (now + timedelta(days=30)).isoformat(),
    }
    if run_id and idempotency_key:
        result_set_record_id = (
            "o.QueryResultSet."
            + hashlib.sha256(
                json.dumps(
                    {
                        "run_id": run_id,
                        "idempotency_key": idempotency_key,
                        "principal_id": principal_id,
                        "workspace_id": workspace_id,
                        "generation": (
                            expired_predecessor.result_set_id
                            if expired_predecessor is not None
                            else "initial"
                        ),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        )
        result_set, created = await QueryResultSet.create_if_absent(
            id=result_set_record_id,
            created_at=now.isoformat(),
            **metadata,
        )
        if not created:
            if result_set.plan_fingerprint != plan_fingerprint:
                raise QuerySpecError(
                    "query.idempotency_conflict: idempotency key reused "
                    "with a different query plan"
                )
            return QuerySpecResult(
                items=None,
                replayed=True,
                result_set_id=result_set.result_set_id,
                normalized_plan=result_set.normalized_plan,
                graph_revision=result_set.graph_revision,
                item_provenance=[
                    QueryItemProvenance.model_validate(item)
                    for item in result_set.item_provenance
                ],
                redaction_state=result_set.redaction_state,
            )
    else:
        await QueryResultSet.create(created_at=now.isoformat(), **metadata)

    return QuerySpecResult(
        items=rows,
        replayed=False,
        result_set_id=result_set_id,
        normalized_plan=normalized_plan,
        graph_revision=graph_revision,
        item_provenance=provenance,
        redaction_state="none",
        next_cursor=next_cursor,
    )


__all__ = ["QuerySpecError", "QuerySpecExecutionError", "execute_query_spec"]
