"""Generic entry-relations endpoints (DR-30-02).

GET /api/entries/{entry_id}/related?relation={field_key}
POST /api/entries/{entry_id}/related/link
DELETE /api/entries/{entry_id}/related/{source_id}?relation={field_key}

Domain-agnostic projection over inbound REFERENCES edges scoped to a
``field_key``. Replaces legacy-era
/api/entries/{entry_id}/related-communications with a generic surface
the frontend can drive for any REFERENCES-shaped relation (email
threads, proposal sources, …).

The listing endpoint preserves the email-thread projection shape used
by the existing frontend (``threads[]`` with subject / message_count /
last_message_at / gmail_thread_id / gmail_labels) so the
RelatedCommunications panel keeps working without a separate adapter.
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceNotFoundError,
)
from app.api.utils import export_node, resolve_principal_id
from app.models.edges import REFERENCES
from app.models.nodes import Entry, EntryType
from app.schemas.policy import Resource, Subject
from app.services.operational_model_compile import _slug
from app.services.policy_engine import evaluate as policy_evaluate


async def _require_entry_read(user_id: str, entry: Entry) -> None:
    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.read",
        resource=Resource(
            kind="entry",
            id=entry.id,
            scope=f"track:{entry.track_id or ''}",
        ),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")


async def _require_entry_update(user_id: str, entry: Entry) -> None:
    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.update",
        resource=Resource(
            kind="entry",
            id=entry.id,
            scope=f"track:{entry.track_id or ''}",
        ),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")


@endpoint(
    "/entries/{entry_id}/related",
    methods=["GET"],
    auth=True,
    tags=["Entries"],
)
async def list_entry_relations(request: Request, entry_id: str) -> Dict[str, Any]:
    """List Entries linked to this Entry via REFERENCES.

    Query param: ``relation`` — OPTIONAL, the ``field_key`` value on the
    REFERENCES edge to scope by (e.g. ``related_communications``). Omit it to
    get every inbound relation, each row carrying the ``relation`` it came in
    on.

    ``relation`` used to be required, and that made the tool unusable for the
    question agents actually ask: "what points at this entry?" A caller cannot
    name a field_key it has not discovered yet, so the required param turned a
    grounding step into a 400. Measured on the Personal Context walk: 7 of 13
    calls to ``integral_get_related`` returned ``bad_request``, and because the
    orchestrator retries an errored call with identical arguments, the third
    attempt tripped the repeat guard and ended the turn — the correction card
    the person asked for was never staged.

    Filtering still works exactly as before when ``relation`` is passed.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    relation = request.query_params.get("relation") or ""
    entry_type_raw = (
        request.query_params.get("entry_type")
        or request.query_params.get("entry_types")
        or ""
    )
    entry_type_filters = [
        s.strip() for s in entry_type_raw.replace(";", ",").split(",") if s.strip()
    ]
    for extra in request.query_params.getlist("entry_type"):
        if extra.strip() and extra.strip() not in entry_type_filters:
            entry_type_filters.append(extra.strip())
    cursor = request.query_params.get("cursor")
    raw_limit = request.query_params.get("limit")
    paginated = bool(cursor) or raw_limit is not None
    try:
        limit = int(raw_limit) if raw_limit is not None else 50
    except ValueError:
        limit = 50
    limit = max(1, min(limit, 200))

    target = await Entry.get(entry_id)
    if target is None:
        raise ResourceNotFoundError(
            message=f"Entry {entry_id!r} not found",
            details={"entry_id": entry_id},
        )
    await _require_entry_read(user_id, target)

    # Walk inbound REFERENCES — convention: source entries write
    # ``REFERENCES → target`` carrying ``field_key=<relation>``.
    related_in = await target.nodes(edge=["REFERENCES"], direction="in", node=["Entry"])
    # Apply the type restriction before pagination. Otherwise a page filled
    # with payslips could hide the matching NIS/PAYE filings entirely.
    if entry_type_filters:
        typed_related = []
        for source in related_in:
            if not isinstance(source, Entry) or not getattr(source, "type_id", None):
                continue
            source_et = await EntryType.get(source.type_id)
            if source_et and any(
                _slug(str(source_et.name)) == _slug(entry_type)
                for entry_type in entry_type_filters
            ):
                typed_related.append(source)
        related_in = typed_related
    next_cursor: Any = None
    has_more = False
    if paginated:
        from app.services.pagination import paginate_list

        related_in = [n for n in related_in if isinstance(n, Entry)]
        related_in.sort(
            key=lambda n: (getattr(n, "created_at", "") or "", n.id), reverse=True
        )
        related_in, next_cursor, has_more = paginate_list(
            related_in,
            cursor,
            limit,
            key_fn=lambda n: (n.id, getattr(n, "created_at", "") or ""),
        )
    ctx = await target.get_context()
    out: List[Dict[str, Any]] = []
    # Generic entry projection, additive alongside the legacy ``threads``
    # shape above (kept as-is for the Gmail-thread consumer). Backs the
    # ``reverse_relation_list`` region-system view type — e.g. "Payslips for
    # this Pay Run" — any caller that wants real Entry rows rather than the
    # email-thread-specific fields.
    entries_out: List[Dict[str, Any]] = []
    for source in related_in:
        if not isinstance(source, Entry):
            continue
        source_decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action="entry.read",
            resource=Resource(
                kind="entry",
                id=source.id,
                scope=f"track:{source.track_id or ''}",
            ),
        )
        if not source_decision.allowed:
            continue
        edges = await ctx.find_edges_between(
            source.id, target.id, edge_class=REFERENCES
        )
        field_keys = [(getattr(e, "field_key", "") or "") for e in edges]
        if relation:
            if relation not in field_keys:
                continue
            matched = relation
        else:
            # Unfiltered: report the key this row arrived on so the caller can
            # narrow a follow-up call without a separate schema round-trip.
            matched = next((k for k in field_keys if k), "")
        cf = getattr(source, "custom_fields", {}) or {}
        out.append(
            {
                "id": source.id,
                "relation": matched,
                "title": getattr(source, "title", "") or "",
                "subject": cf.get("subject") or "",
                "message_count": cf.get("message_count") or 0,
                "last_message_at": cf.get("last_message_at") or "",
                "gmail_thread_id": cf.get("gmail_thread_id") or "",
                "gmail_labels": cf.get("gmail_labels") or [],
            }
        )
        entries_out.append(await export_node(source))
    out.sort(key=lambda r: r.get("last_message_at") or "", reverse=True)
    entries_out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    response: Dict[str, Any] = {"threads": out, "entries": entries_out}
    if paginated:
        response["next_cursor"] = next_cursor
        response["has_more"] = has_more
    return response


@endpoint(
    "/entries/{entry_id}/related/link",
    methods=["POST"],
    auth=True,
    tags=["Entries"],
)
async def link_entry_relation(request: Request, entry_id: str) -> Dict[str, Any]:
    """Wire a REFERENCES edge ``source → entry_id`` scoped by ``relation``.

    Body: ``{source_id: <Entry id>, relation: <field_key>}``. The edge
    carries ``cross_track=true`` and a stable ``relation_type="manual"``
    marker. Idempotent — re-linking the same pair returns
    ``status="already_linked"``.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    body = await request.json() if request.method == "POST" else {}
    source_id = (body.get("source_id") or "").strip()
    relation = (body.get("relation") or "").strip()
    if not source_id or not relation:
        raise BadRequestError(
            message="'source_id' and 'relation' are required",
            details={"hint": "POST {source_id, relation}"},
        )

    target = await Entry.get(entry_id)
    if target is None:
        raise ResourceNotFoundError(
            message=f"Entry {entry_id!r} not found",
            details={"entry_id": entry_id},
        )
    await _require_entry_update(user_id, target)
    source = await Entry.get(source_id)
    if source is None:
        raise ResourceNotFoundError(
            message=f"Source Entry {source_id!r} not found",
            details={"source_id": source_id},
        )
    await _require_entry_read(user_id, source)

    ctx = await source.get_context()
    existing = await ctx.find_edges_between(source.id, target.id, edge_class=REFERENCES)
    for edge in existing:
        if (getattr(edge, "field_key", "") or "") == relation:
            return {
                "status": "already_linked",
                "entry_id": entry_id,
                "source_id": source_id,
                "relation": relation,
            }
    await source.connect(
        target,
        edge=REFERENCES,
        field_key=relation,
        relation_type="manual",
        cross_track=True,
    )
    return {
        "status": "linked",
        "entry_id": entry_id,
        "source_id": source_id,
        "relation": relation,
    }


@endpoint(
    "/entries/{entry_id}/related/{source_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Entries"],
)
async def unlink_entry_relation(
    request: Request, entry_id: str, source_id: str
) -> Dict[str, Any]:
    """Remove REFERENCES edges ``source → entry`` scoped to ``?relation=<key>``."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    relation = request.query_params.get("relation") or ""
    if not relation:
        raise BadRequestError(
            message="'relation' query parameter is required",
            details={"hint": "pass ?relation=<field_key>"},
        )
    target = await Entry.get(entry_id)
    source = await Entry.get(source_id)
    if target is None or source is None:
        raise ResourceNotFoundError(
            message="Entry or source not found",
            details={"entry_id": entry_id, "source_id": source_id},
        )
    await _require_entry_update(user_id, target)
    ctx = await source.get_context()
    edges = await ctx.find_edges_between(source.id, target.id, edge_class=REFERENCES)
    removed = 0
    for edge in edges:
        if (getattr(edge, "field_key", "") or "") == relation:
            await edge.delete()
            removed += 1
    return {"removed": removed}
