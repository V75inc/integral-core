"""Bounded Entry selection with current canonical audience admission."""

from __future__ import annotations

import base64
import heapq
import json
from functools import cmp_to_key, wraps
from typing import Any, AsyncIterator

from jvspatial.core.context import get_default_context
from jvspatial.core.pager import (
    decode_keyset_cursor,
    encode_keyset_cursor,
    keyset_filter,
    keyset_sort_fields,
)
from jvspatial.db.database import resolve_sort_value

from app.exceptions import AuthorizationUnavailableError, BadRequestError
from app.models.edges import COLLABORATES_ON, CONTAINS, EXCLUDED_FROM, OWNS
from app.models.nodes import Entry, Track, Workspace
from app.services.authority_facts import relationship_literal
from app.services.resource_location import (
    DATA_APP_STATES,
    locate_resource,
    locate_resources,
)

WINDOW = 500
METADATA_WINDOW = 2500
_AUTHORITY_FIELDS = [
    "id",
    "entity",
    "context.track_id",
    "context.visibility",
    "track_id",
    "visibility",
]


def _encode_position(record: dict, fields: list[tuple[str, int]]) -> str:
    if len(fields) <= 2:
        return encode_keyset_cursor(record, fields)
    payload = {
        "id": record["id"],
        "fields": [list(field) for field in fields],
        "values": [resolve_sort_value(record, field) for field, _ in fields],
    }
    return base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode()


def _after_position(
    fields: list[tuple[str, int]], position: dict | None
) -> dict | None:
    if position is None:
        return None
    if len(fields) <= 2:
        return _cursor_null_predicates(keyset_filter(fields, position))
    values = position.get("values")
    if (
        position.get("fields") != [list(field) for field in fields]
        or not isinstance(values, list)
        or len(values) != len(fields)
        or not isinstance(position.get("id"), str)
        or any(
            value is not None and not isinstance(value, (str, int, float, bool))
            for value in values
        )
    ):
        raise BadRequestError(message="Cursor does not match the Entry sort")
    prefix: list[dict[str, Any]] = []
    alternatives: list[dict[str, Any]] = []
    for (field, direction), value in zip(fields, values):
        if field == "id" and value != position["id"]:
            raise BadRequestError(message="Invalid Entry cursor")
        if value is not None:
            later: dict[str, Any] = {field: {"$lt" if direction < 0 else "$gt": value}}
            # The adapters put null/missing values last in either direction.
            if field != "id":
                later = {"$or": [later, {field: {"$exists": False}}]}
            alternatives.append(merge_queries(*prefix, later))
        prefix.append({field: {"$exists": False} if value is None else value})
    return {"$or": alternatives} if alternatives else {"id": {"$in": []}}


def _cursor_null_predicates(query: dict | None) -> dict | None:
    """Use the shared null/missing predicate in both persistence adapters.

    jvspatial 0.1.1's PostgreSQL plain-null equality selects SQL-missing
    values but misses explicit JSON null. Its exists-false operator and the
    JSON adapter both select the complete trailing null/missing sort run.
    """
    if query is None:
        return None
    return {
        key: (
            [_cursor_null_predicates(part) for part in value]
            if key in {"$and", "$or"}
            else {"$exists": False} if value is None else value
        )
        for key, value in query.items()
    }


def _unavailable_on_source_error(function):
    @wraps(function)
    async def guarded(*args: Any, **kwargs: Any) -> Any:
        try:
            return await function(*args, **kwargs)
        except (AuthorizationUnavailableError, BadRequestError):
            raise
        except Exception as exc:
            raise AuthorizationUnavailableError() from exc

    return guarded


def merge_queries(*queries: dict[str, Any]) -> dict[str, Any]:
    """Intersect candidate, filter and cursor predicates without overwrites."""
    parts = [query for query in queries if query]
    return parts[0] if len(parts) == 1 else {"$and": parts} if parts else {}


async def direct_entry_ids(user: Any) -> list[str]:
    """Discover complete raw direct candidates; hydration cannot hide a target."""
    from app.services.permissions import ROLE_RANK

    context = await user.get_context()
    last_id = None
    result: list[str] = []
    while True:
        query = {
            "source": user.id,
            "entity": {"$in": [OWNS.__name__, COLLABORATES_ON.__name__]},
        }
        if last_id is not None:
            query["id"] = {"$gt": last_id}
        rows = await context.database.find(
            "edge", query, limit=WINDOW + 1, sort=[("id", 1)]
        )
        page = rows[:WINDOW]
        targets = set()
        for row in page:
            if (
                row.get("source") != user.id
                or row.get("entity") not in {OWNS.__name__, COLLABORATES_ON.__name__}
                or not isinstance(row.get("target"), str)
                or not row["target"]
            ):
                raise AuthorizationUnavailableError()
            relationship_literal(
                row, "role", {"owner"} if row["entity"] == OWNS.__name__ else ROLE_RANK
            )
            targets.add(row["target"])
        if targets:
            entries = await context.database.find(
                "node",
                {"id": {"$in": sorted(targets)}, "entity": Entry._entity_name()},
                limit=len(targets) + 1,
            )
            ids = [row.get("id") for row in entries]
            if len(ids) != len(set(ids)) or not set(ids).issubset(targets):
                raise AuthorizationUnavailableError()
            result.extend(ids)
        if len(rows) <= WINDOW:
            from app.services.share_grant_sources import share_grant_targets

            result.extend(await share_grant_targets(user.id, "entry"))
            return list(dict.fromkeys(result))
        next_id = page[-1].get("id")
        if not isinstance(next_id, str) or (last_id is not None and next_id <= last_id):
            raise AuthorizationUnavailableError()
        last_id = next_id


@_unavailable_on_source_error
async def candidate_query(
    user_id: str,
    *,
    track_id: str | None = None,
    app_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any] | None:
    """Selectors discover candidates; they do not confer a parent read grant."""
    from app.services.permissions import get_user_accessible_tracks, get_user_node

    # Discovery is deliberately independent of parent permission. Canonical
    # binding and the live principal are checked for every selected raw window.
    if track_id:
        return {"context.track_id": track_id}
    if workspace_id:
        context = get_default_context()
        ids = []
        position = None
        while True:
            query = {
                "entity": Track._entity_name(),
                "context.workspace_id": workspace_id,
            }
            if position is not None:
                query["id"] = {"$gt": position}
            rows = await context.database.find(
                "node", query, limit=WINDOW + 1, sort=[("id", 1)]
            )
            page = rows[:WINDOW]
            page_ids = [row.get("id") for row in page]
            if any(
                not isinstance(node_id, str) or not node_id for node_id in page_ids
            ) or len(set(page_ids)) != len(page_ids):
                raise AuthorizationUnavailableError()
            ids.extend(page_ids)
            if len(rows) <= WINDOW:
                return {"context.track_id": {"$in": ids}} if ids else None
            if position is not None and page_ids[-1] <= position:
                raise AuthorizationUnavailableError()
            position = page_ids[-1]
    user = await get_user_node(user_id)
    if user is None:
        return None
    if app_id:
        location = await locate_resource(
            "app", app_id, allowed_app_states=DATA_APP_STATES
        )
        if location is None:
            return None
    tracks = await get_user_accessible_tracks(user_id)
    track_ids = [
        track.id
        for track in tracks
        if not workspace_id or track.workspace_id == workspace_id
    ]
    direct_ids = await direct_entry_ids(user)
    sources = []
    if track_ids:
        sources.append({"context.track_id": {"$in": track_ids}})
    if direct_ids:
        sources.append({"id": {"$in": direct_ids}})
    # Authorship is discovery only. Current role and scope are still checked
    # on every row; being an author never manufactures a read/write grant.
    if not workspace_id and not app_id:
        aliases = [value for value in {user.id, user.user_id} if value]
        sources.append({"context.author_id": {"$in": aliases}})
    return {"$or": sources} if sources else None


def _parent_navigation_record(
    records: dict[str, dict[str, Any]],
    node_id: str,
    workspace_id: str,
    label: str,
) -> dict[str, Any]:
    context = records[node_id]["context"]
    return {
        "id": node_id,
        label: context.get(label, ""),
        "icon": context.get("icon", ""),
        "workspace_id": workspace_id,
    }


async def admit_records(
    user_id: str,
    records: list[dict],
    *,
    workspace_id: str | None = None,
    track_id: str | None = None,
    app_id: str | None = None,
    allowed_app_states: frozenset[str] = DATA_APP_STATES,
) -> list[dict]:
    """Check raw candidate cardinality, containment and grants before exposure."""
    from app.services.permissions import (
        _direct_role_from_rows,
        get_user_node,
        resolve_role,
    )
    from app.services.workspace_governance import owner_governance_allowed
    from app.services.workspace_permissions import _current_workspace_role

    if not records:
        return []
    ids = [row.get("id") for row in records]
    if (
        any(not isinstance(node_id, str) or not node_id for node_id in ids)
        or len(ids) != len(set(ids))
        or any(row.get("entity") != Entry._entity_name() for row in records)
    ):
        raise AuthorizationUnavailableError()
    user = await get_user_node(user_id)
    if user is None:
        return []
    canonical_records: dict[str, dict[str, Any]] = {}
    locations = await locate_resources(
        "entry",
        ids,
        workspace_hints=[workspace_id] if workspace_id else [],
        allowed_app_states=allowed_app_states,
        selected_records=records,
        records_out=canonical_records,
        validate_entry_payload=False,
    )
    context = await user.get_context()
    if owner_governance_allowed():
        # Match point resolution: protected ownership is an independent grant,
        # not an inherited role supplemented by per-Entry collaborator facts.
        # Complete containment and authority validation above still apply; the
        # ordinary/delegated authority mode never takes this path.
        owner_workspaces: dict[str, str] = {}
        owner_rows = []
        for record in records:
            location = locations.get(record["id"])
            if (
                location is None
                or (workspace_id and location.workspace_id != workspace_id)
                or (track_id and location.track_id != track_id)
                or (app_id and location.app_id != app_id)
            ):
                break
            if location.workspace_id not in owner_workspaces:
                workspace = await context._deserialize_entity(
                    Workspace, canonical_records[location.workspace_id]
                )
                owner_workspaces[location.workspace_id] = (
                    await _current_workspace_role(user, workspace)
                    if workspace
                    else "none"
                )
            if owner_workspaces[location.workspace_id] != "owner":
                break
            owner_rows.append(
                {
                    **canonical_records[record["id"]],
                    "__parent_navigation": {
                        "track": _parent_navigation_record(
                            canonical_records,
                            location.track_id,
                            location.workspace_id,
                            "title",
                        ),
                        "app": (
                            _parent_navigation_record(
                                canonical_records,
                                location.app_id,
                                location.workspace_id,
                                "name",
                            )
                            if location.app_id
                            else None
                        ),
                    },
                }
            )
        if len(owner_rows) == len(records):
            return owner_rows
    grants = await context.database.find(
        "edge",
        {
            "source": user.id,
            "target": {"$in": ids},
            "entity": {"$in": [OWNS.__name__, COLLABORATES_ON.__name__]},
        },
        limit=2 * len(ids) + 1,
    )
    groups: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in ids}
    if len(grants) > 2 * len(ids):
        raise AuthorizationUnavailableError()
    for row in grants:
        if (
            row.get("source") != user.id
            or row.get("target") not in groups
            or row.get("entity") not in {OWNS.__name__, COLLABORATES_ON.__name__}
        ):
            raise AuthorizationUnavailableError()
        groups[row["target"]].append(row)
    direct = {node_id: _direct_role_from_rows(rows) for node_id, rows in groups.items()}
    denials = await context.database.find(
        "edge",
        {"source": user.id, "target": {"$in": ids}, "entity": EXCLUDED_FROM.__name__},
        limit=len(ids) + 1,
    )
    if len(denials) > len(ids) or any(
        row.get("source") != user.id
        or row.get("target") not in groups
        or row.get("entity") != EXCLUDED_FROM.__name__
        for row in denials
    ):
        raise AuthorizationUnavailableError()
    denied = {row["target"] for row in denials}
    workspace_roles: dict[str, str] = {}
    parent_roles: dict[str, str | None] = {}
    from app.services.share_grant_sources import share_grant_targets

    sourced_targets = await share_grant_targets(user.id, "entry")
    scoped_locations = {
        node_id: location
        for node_id, location in locations.items()
        if location is not None
        and (not workspace_id or location.workspace_id == workspace_id)
        and (not track_id or location.track_id == track_id)
        and (not app_id or location.app_id == app_id)
    }
    for location in scoped_locations.values():
        if location.workspace_id not in workspace_roles:
            workspace = await context._deserialize_entity(
                Workspace, canonical_records[location.workspace_id]
            )
            workspace_roles[location.workspace_id] = (
                await _current_workspace_role(user, workspace) if workspace else "none"
            )
    from app.services.resource_access_blocks import blocked_entry_ids

    blocked = await blocked_entry_ids(
        user,
        {
            node_id: location
            for node_id, location in scoped_locations.items()
            if not (
                workspace_roles[location.workspace_id] == "owner"
                and owner_governance_allowed()
            )
        },
    )
    result = []
    for selected in records:
        record = canonical_records[selected["id"]]
        location = locations[record["id"]]
        if (
            location is None
            or (workspace_id and location.workspace_id != workspace_id)
            or (track_id and location.track_id != track_id)
            or (app_id and location.app_id != app_id)
        ):
            continue

        record = {
            **record,
            "__parent_navigation": {
                "track": _parent_navigation_record(
                    canonical_records, location.track_id, location.workspace_id, "title"
                ),
                "app": (
                    _parent_navigation_record(
                        canonical_records,
                        location.app_id,
                        location.workspace_id,
                        "name",
                    )
                    if location.app_id
                    else None
                ),
            },
        }
        membership = workspace_roles[location.workspace_id]
        if record["id"] in blocked:
            continue
        if membership == "owner" and owner_governance_allowed():
            result.append(record)
            continue
        if membership == "none":
            # Retained authenticated-public compatibility still uses the
            # exact point resolver, including its deny and read-only ceiling.
            if await resolve_role(user_id, "entry", record["id"]) is not None:
                result.append(record)
            continue
        if direct[record["id"]] is not None:
            result.append(record)
            continue
        if record["id"] in sourced_targets:
            if await resolve_role(user_id, "entry", record["id"]) is not None:
                result.append(record)
                continue
        if record["id"] in denied:
            continue
        if location.track_id not in parent_roles:
            parent_roles[location.track_id] = await resolve_role(
                user_id, "track", location.track_id
            )
        if parent_roles[location.track_id] is not None:
            result.append(record)
    return result


async def iter_records(
    user_id: str,
    query: dict[str, Any],
    *,
    workspace_id: str | None = None,
    track_id: str | None = None,
    app_id: str | None = None,
    sort: list[tuple[str, int]] | None = None,
    batch_size: int = WINDOW,
    cursor: str | None = None,
    allowed_app_states: frozenset[str] = DATA_APP_STATES,
    projection: list[str] | None = None,
) -> AsyncIterator[list[dict]]:
    """Advance by raw rows, including rejected/unhydratable candidate windows."""
    context = get_default_context()
    fields = keyset_sort_fields(sort)
    projected_reader = getattr(context.database, "find_projected", None)
    projected = projection is not None and callable(projected_reader)
    native_projection = projected and getattr(
        context.database, "supports_native_projected_reads", False
    )
    ceiling = METADATA_WINDOW if native_projection else WINDOW
    size = max(1, min(ceiling, batch_size))
    projected_fields = list(
        dict.fromkeys(_AUTHORITY_FIELDS + (projection or []) + [f for f, _ in fields])
    )
    position = decode_keyset_cursor(cursor)
    if position and "ts" in position and "sort" not in position:
        position = {"id": position.get("id"), "sort": position["ts"]}
    try:
        while True:
            after = _after_position(fields, position)
            candidate = merge_queries(
                {"entity": Entry._entity_name()}, query, after or {}
            )
            if projected:
                flat_rows = await projected_reader(
                    "node",
                    candidate,
                    fields=projected_fields,
                    limit=size + 1,
                    sort=fields,
                )
                rows = []
                for flat in flat_rows:
                    if not isinstance(flat, dict) or not set(flat).issubset(
                        projected_fields
                    ):
                        raise AuthorizationUnavailableError()
                    # A projection is an internal authority/ranking envelope,
                    # never an Entry payload. Missing and explicit null stay
                    # distinct; do not fill model defaults into these records.
                    row: dict[str, Any] = {"context": {}, "__entry_projection": True}
                    for path, value in flat.items():
                        target = row
                        segments = path.split(".")
                        for segment in segments[:-1]:
                            target = target.setdefault(segment, {})
                        target[segments[-1]] = value
                    rows.append(row)
                legacy = [
                    row["id"]
                    for row in rows
                    if "track_id" not in row["context"]
                    or "visibility" not in row["context"]
                ]
                if legacy:
                    # A missing canonical field can be a legitimate legacy
                    # representation, or a malformed context container. Read
                    # those complete records rather than inventing context={}.
                    complete = await context.database.find(
                        "node", {"id": {"$in": legacy}}, limit=len(legacy) + 1
                    )
                    by_id = {row.get("id"): row for row in complete}
                    if len(complete) != len(legacy) or set(by_id) != set(legacy):
                        raise AuthorizationUnavailableError()
                    rows = [by_id.get(row["id"], row) for row in rows]
            else:
                rows = await context.database.find(
                    "node", candidate, limit=size + 1, sort=fields
                )
            page = rows[:size]
            admitted = await admit_records(
                user_id,
                page,
                workspace_id=workspace_id,
                track_id=track_id,
                app_id=app_id,
                allowed_app_states=allowed_app_states,
            )
            if admitted:
                yield admitted
            if len(rows) <= size:
                return
            next_position = decode_keyset_cursor(_encode_position(page[-1], fields))
            if next_position is None or next_position == position:
                raise AuthorizationUnavailableError()
            position = next_position
    except (AuthorizationUnavailableError, BadRequestError):
        raise
    except Exception as exc:
        raise AuthorizationUnavailableError() from exc


@_unavailable_on_source_error
async def hydrate_records(records: list[dict]) -> list[Entry]:
    """An admitted row that cannot materialize is unavailable, never invisible."""
    context = get_default_context()
    result = []
    for record in records:
        if record.get("__entry_projection"):
            raise AuthorizationUnavailableError()
        node = await context._deserialize_entity(Entry, record)
        if node is None:
            raise AuthorizationUnavailableError()
        node._read_parent_navigation = record.get("__parent_navigation")
        result.append(node)
    return result


@_unavailable_on_source_error
async def count_bound_records(
    user_id: str,
    track_id: str,
    query: dict[str, Any],
    *,
    workspace_id: str | None = None,
    app_id: str | None = None,
) -> int | None:
    """Use optional storage facts only after current protected-owner admission.

    The framework supplies literal health and one-hop cardinality, never a
    permission. Ordinary authority, unsupported adapters/predicates and bounded
    aggregate overflow retain the canonical streaming executor. No summary is
    reused across calls, and a projected/partial total is never returned.
    """
    from app.services.permissions import get_user_node
    from app.services.workspace_governance import owner_governance_allowed
    from app.services.workspace_permissions import _current_workspace_role

    context = get_default_context()
    if not owner_governance_allowed() or not getattr(
        context.database, "supports_native_graph_binding_summary", False
    ):
        return None
    reader = getattr(context.database, "summarize_graph_bindings", None)
    if not callable(reader):
        return None
    candidate = merge_queries(
        {"entity": Entry._entity_name()}, query, {"context.track_id": track_id}
    )
    if not await context.database.find_ids("node", candidate, limit=1):
        # The canonical stream also returns zero without an audience to admit.
        # A raw empty result grants nothing; source failures still propagate.
        # This avoids repeating ancestor/role discovery for empty partitions.
        return 0

    # Feature-detected: the published baseline need not expose these new types.
    from jvspatial.db.graph_bindings import GraphBindingSummary, LiteralBindingFact

    async def current_owner_location():
        user = await get_user_node(user_id)
        if user is None:
            return None
        records: dict[str, dict] = {}
        locations = await locate_resources(
            "track",
            [track_id],
            records_out=records,
            workspace_hints=[workspace_id] if workspace_id else [],
            allowed_app_states=DATA_APP_STATES,
        )
        location = locations.get(track_id)
        if (
            location is None
            or (workspace_id and location.workspace_id != workspace_id)
            or (app_id and location.app_id != app_id)
        ):
            return None
        workspace = await context._deserialize_entity(
            Workspace, records[location.workspace_id]
        )
        if (
            workspace is None
            or await _current_workspace_role(user, workspace) != "owner"
        ):
            return None
        return location

    before = await current_owner_location()
    if before is None:
        return None
    summary = await reader(
        candidate,
        record_entity=Entry._entity_name(),
        edge_entity=CONTAINS._entity_name(),
        parent_id=track_id,
        facts=(
            LiteralBindingFact("context.track_id", (track_id,), "track_id"),
            LiteralBindingFact(
                "context.visibility", ("inherit", "private"), "visibility"
            ),
        ),
    )
    if summary is None:
        return None
    if not isinstance(summary, GraphBindingSummary):
        raise AuthorizationUnavailableError()
    if not summary.complete:
        return None
    if summary.invalid_facts or summary.invalid_edges:
        raise AuthorizationUnavailableError()
    if not owner_governance_allowed() or await current_owner_location() != before:
        raise AuthorizationUnavailableError()
    return summary.bound


def _compare_values(
    left: list[Any], right: list[Any], fields: list[tuple[str, int]]
) -> int:
    for a, b, (_, direction) in zip(left, right, fields):
        if a is None or b is None:
            if a is not b:
                return 1 if a is None else -1
        elif a != b:
            return (-1 if a < b else 1) * direction
    return 0


def _record_values(record: dict, fields: list[tuple[str, int]]) -> list[Any]:
    return [resolve_sort_value(record, field) for field, _ in fields]


def _cursor_values(
    position: dict | None, fields: list[tuple[str, int]]
) -> list[Any] | None:
    if position is None:
        return None
    if len(fields) > 2:
        if position.get("fields") != [list(field) for field in fields]:
            raise BadRequestError(message="Cursor does not match the Entry sort")
        values = position.get("values")
    else:
        values = (
            [position.get("sort", position.get("ts")), position.get("id")]
            if len(fields) == 2
            else [position.get("id")]
        )
    if (
        not isinstance(values, list)
        or len(values) != len(fields)
        or not isinstance(position.get("id"), str)
    ):
        raise BadRequestError(message="Invalid Entry cursor")
    for (field, _), value in zip(fields, values):
        if field == "id" and value != position["id"]:
            raise BadRequestError(message="Invalid Entry cursor")
    return values


@_unavailable_on_source_error
async def _select_portable_page(
    user_id: str,
    query: dict[str, Any],
    *,
    fields: list[tuple[str, int]],
    limit: int,
    offset: int,
    cursor: str | None,
    include_total: bool,
    workspace_id: str | None,
    track_id: str | None,
    app_id: str | None,
) -> tuple[list[Entry], dict]:
    """Rank admitted rows with bounded windows and a page/offset-sized heap.

    jvspatial 0.1.1 PostgreSQL sorts custom values as text, while its JSON
    adapter uses native values. Scalar/custom sorts need this portable path:
    an adapter LIMIT before numeric ordering otherwise loses the true top N.
    Exact-total reads also use this single ID-ordered admission scan, avoiding
    repeated database sorts of the whole audience for each count window.
    Reads without totals keep indexed storage keyset ordering when supported.
    Every raw window still receives current canonical admission.
    """
    after = _cursor_values(decode_keyset_cursor(cursor), fields)

    def compare(a: dict, b: dict) -> int:
        return _compare_values(
            _record_values(a, fields), _record_values(b, fields), fields
        )

    worst_first = cmp_to_key(lambda a, b: -compare(a, b))
    heap: list[Any] = []
    capacity = offset + limit + 1
    total = 0
    remaining = 0
    async for records in iter_records(
        user_id,
        query,
        workspace_id=workspace_id,
        track_id=track_id,
        app_id=app_id,
        sort=[("id", 1)],
        projection=[field for field, _ in fields],
        batch_size=METADATA_WINDOW,
    ):
        total += len(records)
        for record in records:
            if (
                after is not None
                and _compare_values(_record_values(record, fields), after, fields) <= 0
            ):
                continue
            remaining += 1
            item = worst_first(record)
            if len(heap) < capacity:
                heapq.heappush(heap, item)
            elif compare(record, heap[0].obj) < 0:
                heapq.heapreplace(heap, item)
    ordered = sorted([item.obj for item in heap], key=cmp_to_key(compare))
    page = ordered[offset : offset + limit]
    has_more = remaining > offset + len(page)
    if page and page[0].get("__entry_projection"):
        context = get_default_context()
        ids = [record["id"] for record in page]
        current = await context.database.find(
            "node", {"id": {"$in": ids}}, limit=len(ids) + 1
        )
        by_id = {record.get("id"): record for record in current}
        if len(current) != len(ids) or set(by_id) != set(ids):
            raise AuthorizationUnavailableError()
        # Selection isn't a permission lease. Fetch the complete current
        # payload and repeat canonical admission before rendering any Entry.
        current = await admit_records(
            user_id,
            [by_id[node_id] for node_id in ids],
            workspace_id=workspace_id,
            track_id=track_id,
            app_id=app_id,
        )
        if [record["id"] for record in current] != ids:
            raise AuthorizationUnavailableError()
        hydrated = await hydrate_records(current)
    else:
        hydrated = await hydrate_records(page)
    return hydrated, {
        "total": total if include_total else None,
        "has_more": has_more,
        "next_cursor": (
            _encode_position(page[-1], fields) if has_more and page else None
        ),
    }


async def select_page(
    user_id: str,
    query: dict[str, Any],
    *,
    limit: int,
    offset: int = 0,
    cursor: str | None = None,
    include_total: bool = True,
    sort: list[tuple[str, int]] | None = None,
    workspace_id: str | None = None,
    track_id: str | None = None,
    app_id: str | None = None,
) -> tuple[list[Entry], dict]:
    """Count the admitted audience and refill pages past rejected raw records."""
    fields = keyset_sort_fields(sort)
    if (
        include_total
        and track_id
        and all(
            field in {"id", "context.created_at", "context.updated_at"}
            for field, _ in fields
        )
    ):
        exact = await count_bound_records(
            user_id, track_id, query, workspace_id=workspace_id, app_id=app_id
        )
        if exact is not None:
            # The aggregate proves the exact audience without shipping it to
            # Python. A separate current page still performs canonical
            # admission and full-payload hydration, including denied refill.
            entries, metadata = await select_page(
                user_id,
                query,
                limit=limit,
                offset=offset,
                cursor=cursor,
                include_total=False,
                sort=sort,
                workspace_id=workspace_id,
                track_id=track_id,
                app_id=app_id,
            )
            return entries, {**metadata, "total": exact}
    # The portable comparator is needed for custom/numeric values and
    # labels whose database collation can differ from native ordering.
    # The 50K PostgreSQL fixture repeated 400–470ms sorts per 501-row window
    # when exact totals walked the entire audience in presentation order.
    # A bounded heap over one ID-ordered scan preserves exact scope/counts
    # without requiring a full sorted audience for each window.
    if include_total or any(
        field not in {"id", "context.created_at", "context.updated_at"}
        for field, _ in fields
    ):
        return await _select_portable_page(
            user_id,
            query,
            fields=fields,
            limit=limit,
            offset=offset,
            cursor=cursor,
            include_total=include_total,
            workspace_id=workspace_id,
            track_id=track_id,
            app_id=app_id,
        )
    selected: list[dict[str, Any]] = []
    visible = 0
    async for records in iter_records(
        user_id,
        query,
        workspace_id=workspace_id,
        track_id=track_id,
        app_id=app_id,
        sort=fields,
        cursor=cursor,
        # A scrolling page needs one admitted lookahead, not 500 candidates.
        # iter_records still refills past denied rows and checks every window.
        batch_size=WINDOW if include_total else min(WINDOW, offset + limit + 1),
    ):
        for record in records:
            if visible >= offset and len(selected) <= limit:
                selected.append(record)
            visible += 1
        if not include_total and len(selected) > limit:
            break
    has_more = len(selected) > limit
    page = selected[:limit]
    total = visible if include_total else None
    if include_total and cursor:
        total = 0
        async for records in iter_records(
            user_id,
            query,
            workspace_id=workspace_id,
            track_id=track_id,
            app_id=app_id,
            sort=fields,
        ):
            total += len(records)
    return await hydrate_records(page), {
        "total": total,
        "has_more": has_more,
        "next_cursor": (
            _encode_position(page[-1], fields) if has_more and page else None
        ),
    }
