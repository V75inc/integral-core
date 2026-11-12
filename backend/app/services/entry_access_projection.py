"""Bounded Entry selection with current canonical audience admission."""

from __future__ import annotations

import base64
import heapq
import json
from dataclasses import dataclass
from functools import cmp_to_key, wraps
from typing import Any, AsyncIterator, Awaitable, Callable

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
from app.services.perf_trace import perf_traced
from app.services.resource_location import (
    DATA_APP_STATES,
    ResourceLocation,
    locate_resource,
    locate_resources,
)

WINDOW = 500
METADATA_WINDOW = 2500
# deviation: the default native summary stopped at 100K and fell back to
# streaming. At 250K, a bounded native summary took 1.46 s vs 10.81 s.
# Leave headroom for writes crossing the 500K qualification boundary.
_NATIVE_COUNT_MAX_RECORDS = 550_000
_NATIVE_COUNT_MAX_EDGES = 1_000_000
_AUTHORITY_FIELDS = [
    "id",
    "entity",
    "context.track_id",
    "context.visibility",
    "track_id",
    "visibility",
]


class _BinaryTitleUnavailable(Exception):
    """The adapter cannot match the portable title comparator for this scope."""


def _entry_count_binding_facts(track_id: str):
    from jvspatial.db.graph_bindings import LiteralBindingFact

    return (
        LiteralBindingFact("context.track_id", (track_id,), "track_id"),
        LiteralBindingFact("context.visibility", ("inherit", "private"), "visibility"),
    )


async def configure_entry_count_projection(
    track_id: str, *, rebuild: bool = False
) -> bool:
    """Attach the existing projection, or backfill during explicit maintenance."""
    database = get_default_context().database
    name = (
        "install_graph_binding_projection"
        if rebuild
        else "attach_graph_binding_projection"
    )
    configure = getattr(database, name, None)
    if not track_id or not callable(configure):
        return False
    attached = await configure(
        partition_path="context.track_id",
        record_entity=Entry._entity_name(),
        edge_entity=CONTAINS._entity_name(),
        parent_id=track_id,
        facts=_entry_count_binding_facts(track_id),
    )
    return rebuild or bool(attached)


async def ensure_entry_title_indexes() -> None:
    """Prepare optional storage indexes alongside the other model indexes."""
    database = get_default_context().database
    create = getattr(database, "create_binary_text_keyset_indexes", None)
    if callable(create):
        await create(
            "node",
            field="context.title",
            scope_field="context.track_id",
            entity=Entry._entity_name(),
        )


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
        except (
            AuthorizationUnavailableError,
            BadRequestError,
            _BinaryTitleUnavailable,
        ):
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


async def _projected_records(database, query, *, fields, limit, sort):
    """Build bounded raw authority envelopes, preserving legacy validation."""
    flat_rows = await database.find_projected(
        "node", query, fields=fields, limit=limit, sort=sort
    )
    rows = []
    for flat in flat_rows:
        if not isinstance(flat, dict) or not set(flat).issubset(fields):
            raise AuthorizationUnavailableError()
        # This envelope is never a full Entry payload. Missing and explicit
        # null stay distinct; hydration defaults cannot invent authority.
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
        if "track_id" not in row["context"] or "visibility" not in row["context"]
    ]
    if legacy:
        # Absent canonical fields can mean legacy storage or a corrupt context
        # container. Read their complete primary facts instead of masking it.
        complete = await database.find(
            "node", {"id": {"$in": legacy}}, limit=len(legacy) + 1
        )
        by_id = {row.get("id"): row for row in complete}
        if len(complete) != len(legacy) or set(by_id) != set(legacy):
            raise AuthorizationUnavailableError()
        rows = [by_id.get(row["id"], row) for row in rows]
    return rows


@dataclass(frozen=True)
class _RecordWindow:
    records: list[dict]
    has_more: bool
    raw_count: int


async def _iter_record_windows(
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
    binary_title: bool = False,
) -> AsyncIterator[_RecordWindow]:
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
            after = None if binary_title else _after_position(fields, position)
            candidate = merge_queries(
                {"entity": Entry._entity_name()}, query, after or {}
            )
            if binary_title:
                values = _cursor_values(position, fields)
                if (
                    values is not None
                    and values[0] is not None
                    and not isinstance(values[0], str)
                ):
                    raise _BinaryTitleUnavailable()
                rows = await context.database.find_binary_text_keyset(
                    "node",
                    candidate,
                    field="context.title",
                    scope_field="context.track_id",
                    scope_value=track_id,
                    entity=Entry._entity_name(),
                    direction=fields[0][1],
                    after=(values[0], values[1]) if values is not None else None,
                    limit=size + 1,
                )
                if rows is None:
                    raise _BinaryTitleUnavailable()
            elif projected:
                rows = await _projected_records(
                    context.database,
                    candidate,
                    fields=projected_fields,
                    limit=size + 1,
                    sort=fields,
                )
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
            yield _RecordWindow(admitted, len(rows) > size, len(page))
            if len(rows) <= size:
                return
            next_position = decode_keyset_cursor(_encode_position(page[-1], fields))
            if next_position is None or next_position == position:
                raise AuthorizationUnavailableError()
            position = next_position
    except (AuthorizationUnavailableError, BadRequestError, _BinaryTitleUnavailable):
        raise
    except Exception as exc:
        raise AuthorizationUnavailableError() from exc


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
    """Yield admitted rows while advancing past every rejected raw window."""
    async for window in _iter_record_windows(
        user_id,
        query,
        workspace_id=workspace_id,
        track_id=track_id,
        app_id=app_id,
        sort=sort,
        batch_size=batch_size,
        cursor=cursor,
        allowed_app_states=allowed_app_states,
        projection=projection,
    ):
        if window.records:
            yield window.records


@_unavailable_on_source_error
@perf_traced("entries.hydration")
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


@dataclass(frozen=True)
class _CountAuthority:
    location: ResourceLocation
    profile_id: str
    identity_id: str
    membership_id: str
    parent_role: str | None
    exception_ids: tuple[str, ...]
    allowed_ids: tuple[str, ...]

    @property
    def denied_ids(self) -> tuple[str, ...]:
        allowed = set(self.allowed_ids)
        return tuple(
            node_id for node_id in self.exception_ids if node_id not in allowed
        )


async def _inherited_count_authority(
    user_id: str,
    track_id: str,
    *,
    workspace_id: str | None,
    app_id: str | None,
) -> _CountAuthority | None:
    """Prove a current inherited or bounded explicit Entry audience.

    Every exceptional candidate uses canonical batch admission. The global
    probe is complete and bounded; overflow retains streaming. Actual record
    discriminators, never ID prefixes, identify Entry candidates. No decision
    is cached and the complete proof must match after the aggregate.
    """
    from app.models.access_lifecycle import RESOURCE_ACCESS_BLOCK
    from app.services.permissions import get_user_node, resolve_role
    from app.services.share_grant_sources import share_grant_targets
    from app.services.workspace_permissions import (
        _current_workspace_role,
        read_member_facts,
    )

    user = await get_user_node(user_id)
    if user is None:
        return None
    location = await locate_resource(
        "track", track_id, allowed_app_states=DATA_APP_STATES
    )
    if (
        location is None
        or (workspace_id and location.workspace_id != workspace_id)
        or (app_id and location.app_id != app_id)
    ):
        return None
    context = await user.get_context()
    workspace = await Workspace.find_one({"id": location.workspace_id})
    if workspace is None or await _current_workspace_role(user, workspace) == "none":
        return None
    member = await read_member_facts(user, location.workspace_id)
    role = await resolve_role(user_id, "track", track_id)
    if member is None:
        return None
    kinds = [
        OWNS.__name__,
        COLLABORATES_ON.__name__,
        EXCLUDED_FROM.__name__,
        RESOURCE_ACCESS_BLOCK.__name__,
    ]
    rows = await context.database.find(
        "edge", {"source": user.id, "entity": {"$in": kinds}}, limit=1001
    )
    if len(rows) > 1000 or any(
        row.get("source") != user.id
        or row.get("entity") not in kinds
        or not isinstance(row.get("target"), str)
        or not row["target"]
        for row in rows
    ):
        return None
    targets = {row["target"] for row in rows}
    targets.update(await share_grant_targets(user.id, "entry"))
    if len(targets) > 1000:
        return None
    exceptions = []
    if targets:
        if not getattr(context.database, "supports_native_projected_reads", False):
            return None
        exceptions = await _projected_records(
            context.database,
            {"entity": Entry._entity_name(), "id": {"$in": sorted(targets)}},
            fields=_AUTHORITY_FIELDS,
            limit=1001,
            sort=[("id", 1)],
        )
        if len(exceptions) > 1000:
            return None
    admitted = (
        await admit_records(
            user_id,
            exceptions,
            workspace_id=location.workspace_id,
            track_id=track_id,
            app_id=location.app_id or None,
        )
        if exceptions
        else []
    )
    return _CountAuthority(
        location,
        user.id,
        user.user_id,
        member["id"],
        role,
        tuple(sorted(row["id"] for row in exceptions)),
        tuple(sorted(row["id"] for row in admitted)),
    )


@dataclass(frozen=True)
class _BoundCount:
    total: int
    recheck: Callable[[], Awaitable[bool]] | None
    candidates: dict[str, Any] | None = None


def _audience_candidates(query: dict, authority: _CountAuthority) -> dict:
    """Narrow discovery only; selected rows still require current admission."""
    if authority.parent_role is None:
        return merge_queries(query, {"id": {"$in": list(authority.allowed_ids)}})
    if authority.denied_ids:
        return merge_queries(query, {"id": {"$nin": list(authority.denied_ids)}})
    return query


@_unavailable_on_source_error
async def _native_bound_count(
    user_id: str,
    track_id: str,
    query: dict[str, Any],
    *,
    workspace_id: str | None = None,
    app_id: str | None = None,
) -> _BoundCount | None:
    """Count native bindings after proving a uniform current audience.

    The framework supplies literal health and one-hop cardinality, never a
    permission. Unsupported predicates and bounded authority/aggregate
    overflow retain the canonical streaming executor. No
    summary is reused across calls, and partial totals are never returned.
    """
    from app.services.permissions import get_user_node
    from app.services.workspace_governance import owner_governance_allowed
    from app.services.workspace_permissions import _current_workspace_role

    context = get_default_context()
    if not getattr(context.database, "supports_native_graph_binding_summary", False):
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
        return _BoundCount(0, None)

    # Feature-detected: the published baseline need not expose these new types.
    from jvspatial.db.graph_bindings import GraphBindingSummary

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
        return location, user.id, user.user_id

    protected = owner_governance_allowed()
    before = await current_owner_location() if protected else None
    if before is None:
        protected = False
        before = await _inherited_count_authority(
            user_id, track_id, workspace_id=workspace_id, app_id=app_id
        )
    if before is None:
        return None
    aggregate_query = candidate
    if not protected:
        aggregate_query = _audience_candidates(candidate, before)
    summary = await reader(
        aggregate_query,
        record_entity=Entry._entity_name(),
        edge_entity=CONTAINS._entity_name(),
        parent_id=track_id,
        facts=_entry_count_binding_facts(track_id),
        max_records=_NATIVE_COUNT_MAX_RECORDS,
        max_edges=_NATIVE_COUNT_MAX_EDGES,
    )
    if summary is None:
        return None
    if not isinstance(summary, GraphBindingSummary):
        raise AuthorizationUnavailableError()
    if not summary.complete:
        return None
    if summary.invalid_facts or summary.invalid_edges:
        raise AuthorizationUnavailableError()

    @_unavailable_on_source_error
    async def recheck():
        if protected:
            after = (
                await current_owner_location() if owner_governance_allowed() else None
            )
        else:
            after = await _inherited_count_authority(
                user_id, track_id, workspace_id=workspace_id, app_id=app_id
            )
        return after == before

    if not await recheck():
        raise AuthorizationUnavailableError()
    return _BoundCount(summary.bound, recheck, aggregate_query)


async def count_bound_records(
    user_id: str,
    track_id: str,
    query: dict[str, Any],
    *,
    workspace_id: str | None = None,
    app_id: str | None = None,
) -> int | None:
    """Return one currently admitted native count, or retain portable admission."""
    result = await _native_bound_count(
        user_id, track_id, query, workspace_id=workspace_id, app_id=app_id
    )
    return result.total if result is not None else None


async def count_accessible_records(
    user_id: str,
    query: dict[str, Any],
    *,
    workspace_id: str | None = None,
    track_id: str | None = None,
    app_id: str | None = None,
) -> int:
    """Count the current audience without sorting or hydrating a record page."""
    scope = {"workspace_id": workspace_id, "app_id": app_id}
    if track_id:
        exact = await count_bound_records(user_id, track_id, query, **scope)
        if exact is not None:
            return exact
    total = 0
    async for records in iter_records(
        user_id,
        query,
        track_id=track_id,
        sort=[("id", 1)],
        projection=[],
        batch_size=METADATA_WINDOW,
        **scope,
    ):
        total += len(records)
    return total


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

    Unsupported numeric compiler/type coverage and label ordering use this
    portable comparator. Native numeric support is probed explicitly; it must
    never apply LIMIT to an untranslatable or heterogeneously typed order.
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
    hydrated = (
        await _hydrate_current_page(user_id, page, workspace_id, track_id, app_id)
        if page and page[0].get("__entry_projection")
        else await hydrate_records(page)
    )
    return hydrated, {
        "total": total if include_total else None,
        "has_more": has_more,
        "next_cursor": (
            _encode_position(page[-1], fields) if has_more and page else None
        ),
    }


async def _hydrate_current_page(user_id, page, workspace_id, track_id, app_id):
    """Re-admit complete current records before releasing a selected page."""
    context = get_default_context()
    ids = [record["id"] for record in page]
    current = await context.database.find(
        "node", {"id": {"$in": ids}}, limit=len(ids) + 1
    )
    by_id = {record.get("id"): record for record in current}
    if len(current) != len(ids) or set(by_id) != set(ids):
        raise AuthorizationUnavailableError()
    current = await admit_records(
        user_id,
        [by_id[node_id] for node_id in ids],
        workspace_id=workspace_id,
        track_id=track_id,
        app_id=app_id,
    )
    if [record["id"] for record in current] != ids:
        raise AuthorizationUnavailableError()
    return await hydrate_records(current)


@_unavailable_on_source_error
async def _native_numeric_order(query, fields, cursor=None) -> bool:
    """Use existing numeric storage ordering only with complete type coverage.

    Compiler support cannot certify current values. Reject non-number leaves
    (including booleans), keep null/missing last, and repeat this live proof
    after selection. Labels and unsupported predicates retain the bounded
    portable comparator. No type or authority result survives this call.
    """
    special = [
        field
        for field, _ in fields
        if field not in {"id", "context.created_at", "context.updated_at"}
    ]
    if not special:
        return True
    database = get_default_context().database
    checker = getattr(database, "can_pushdown_numeric_sort", None)
    if not getattr(database, "supports_native_projected_reads", False) or not callable(
        checker
    ):
        return False
    candidate = merge_queries({"entity": Entry._entity_name()}, query)
    if not checker(candidate, fields):
        return False
    if cursor:
        values = _cursor_values(decode_keyset_cursor(cursor), fields)
        if values is not None and any(
            field in special and value is not None and type(value) not in {int, float}
            for (field, _), value in zip(fields, values)
        ):
            return False
    for field in special:
        invalid = merge_queries(
            candidate, {field: {"$ne": None, "$not": {"$type": "number"}}}
        )
        if not checker(invalid, [("id", 1)]):
            return False
        if await database.find_ids("node", invalid, limit=1):
            return False
    return True


@_unavailable_on_source_error
async def _select_native_page(
    user_id: str,
    query: dict[str, Any],
    *,
    fields: list[tuple[str, int]],
    limit: int,
    offset: int,
    cursor: str | None,
    workspace_id: str | None,
    track_id: str | None,
    app_id: str | None,
    refine_candidates: bool = True,
    binary_title: bool = False,
) -> tuple[list[Entry], dict]:
    """Select current rows, narrowing a sparse audience without granting it.

    Ordinary pages do no extra authority work. Only a severely rejected raw
    window triggers a complete bounded discovery proof, which is rechecked
    after selection. The 50K mixed-ACL probe exposed minutes of 21-row refill
    after Track revocation left one direct grant; retaining that count's
    candidates, or refining a sparse no-total page, avoids the private scan.
    """
    selected: list[dict[str, Any]] = []
    visible = 0
    async for window in _iter_record_windows(
        user_id,
        query,
        workspace_id=workspace_id,
        track_id=track_id,
        app_id=app_id,
        sort=fields,
        cursor=cursor,
        batch_size=min(WINDOW, offset + limit + 1),
        binary_title=binary_title,
    ):
        for record in window.records:
            if visible >= offset and len(selected) <= limit:
                selected.append(record)
            visible += 1
        if len(selected) > limit:
            break
        if (
            refine_candidates
            and track_id
            and window.has_more
            and len(window.records) * 2 < window.raw_count
        ):
            refine_candidates = False
            authority = await _inherited_count_authority(
                user_id, track_id, workspace_id=workspace_id, app_id=app_id
            )
            narrowed = _audience_candidates(query, authority) if authority else query
            if narrowed != query:
                # These are candidates, never preauthorized results. Restart
                # at the original position and canonically admit every row.
                result = await _select_native_page(
                    user_id,
                    narrowed,
                    fields=fields,
                    limit=limit,
                    offset=offset,
                    cursor=cursor,
                    workspace_id=workspace_id,
                    track_id=track_id,
                    app_id=app_id,
                    refine_candidates=False,
                    binary_title=binary_title,
                )
                if (
                    await _inherited_count_authority(
                        user_id,
                        track_id,
                        workspace_id=workspace_id,
                        app_id=app_id,
                    )
                    != authority
                ):
                    raise AuthorizationUnavailableError()
                if not binary_title and not await _native_numeric_order(
                    query, fields, cursor
                ):
                    raise AuthorizationUnavailableError()
                return result
    has_more = len(selected) > limit
    page = selected[:limit]
    if not binary_title and not await _native_numeric_order(query, fields, cursor):
        raise AuthorizationUnavailableError()
    hydrated = (
        await _hydrate_current_page(user_id, page, workspace_id, track_id, app_id)
        if binary_title and page
        else await hydrate_records(page)
    )
    return hydrated, {
        "total": None,
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
    binary_title = bool(
        track_id
        and fields
        in (
            [("context.title", 1), ("id", 1)],
            [("context.title", -1), ("id", -1)],
        )
        and callable(
            getattr(get_default_context().database, "find_binary_text_keyset", None)
        )
    )
    native_order = binary_title or await _native_numeric_order(query, fields, cursor)
    try:
        exact = None
        if include_total and track_id and native_order:
            exact = await _native_bound_count(
                user_id, track_id, query, workspace_id=workspace_id, app_id=app_id
            )
        if native_order and (exact is not None or not include_total):
            candidates = exact.candidates if exact is not None else None
            entries, metadata = await _select_native_page(
                user_id,
                candidates if candidates is not None else query,
                fields=fields,
                limit=limit,
                offset=offset,
                cursor=cursor,
                workspace_id=workspace_id,
                track_id=track_id,
                app_id=app_id,
                binary_title=binary_title,
            )
            if exact is not None:
                if exact.recheck is not None and not await exact.recheck():
                    raise AuthorizationUnavailableError()
                if not binary_title and not await _native_numeric_order(
                    query, fields, cursor
                ):
                    raise AuthorizationUnavailableError()
                metadata["total"] = exact.total
            return entries, metadata
    except _BinaryTitleUnavailable:
        # A compiler refusal or malformed legacy sort value requires a fresh
        # portable selection from the original cursor, never mixed ordering.
        pass

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
