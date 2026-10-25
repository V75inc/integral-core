"""Permission-bound passage retrieval shared by REST and the agent surface."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.api.errors import BadRequestError, InsufficientPermissionsError
from app.models.nodes import Entry
from app.schemas.policy import Resource, Subject
from app.schemas.retrieve import RetrievedEntry, RetrievedPassage
from app.services.policy_engine import evaluate

from .embedding_configuration import resolve_workspace_embedding_profile
from .indexing import index_document
from .passage_store import passage_store

logger = logging.getLogger(__name__)


async def _eligible_scope(subject, workspace_id, scope, identities):
    """Admit only the selected IDs without walking all of the user's grants."""
    from app.services.resource_location import DATA_APP_STATES, locate_resources

    locations = await locate_resources(
        "entry",
        identities,
        workspace_hints=[workspace_id],
        allowed_app_states=DATA_APP_STATES,
    )
    admitted = []
    for identity in identities:
        location = locations.get(identity)
        if location is None or location.workspace_id != workspace_id:
            continue
        if scope.startswith("app:") and location.app_id != scope.split(":", 1)[1]:
            continue
        if scope.startswith("track:") and location.track_id != scope.split(":", 1)[1]:
            continue
        decision = await evaluate(
            subject=subject,
            action="entry.read",
            resource=Resource(
                kind="entry", id=identity, scope=f"track:{location.track_id}"
            ),
        )
        if decision.allowed:
            admitted.append(identity)
    return admitted


async def scoped_tracks(
    subject: Subject,
    workspace_id: str,
    scope: str,
    selected_ids: list[str] | None = None,
    scope_inventory: set[str] | None = None,
) -> list[str]:
    """Resolve current readable Tracks inside the exact host-bound scope."""
    from app.services.resource_location import DATA_APP_STATES, locate_resources

    cursor = None
    registry = None
    tracks = []
    if selected_ids is not None:
        if len(selected_ids) > 500:
            raise BadRequestError(message="Narrow this search to fewer lists")
        records = [{"id": identity} for identity in selected_ids]
    elif scope.startswith("track:"):
        records = [{"id": scope.split(":", 1)[1]}]
    elif scope.startswith("app:"):
        from app.models.edges import CONTAINS
        from app.models.nodes import App

        app = await App.find_one({"id": scope.split(":", 1)[1]})
        children = (
            await app.nodes(edge=[CONTAINS], node=["Track"], limit=5001) if app else []
        )
        if len(children) > 5000:
            raise BadRequestError(message="Narrow this search to a list")
        records = [{"id": child.id} for child in children]
    else:
        records = None
        from app.models.edges import CATALOGS, CONTAINS
        from app.models.nodes import Workspace
        from app.services.app_graph import _workspace_branch_id

        workspace = await Workspace.find_one({"id": workspace_id})
        branches = (
            await workspace.nodes(
                edge=[CONTAINS],
                node=[{"Tracks": {"id": _workspace_branch_id(workspace_id, "tracks")}}],
                limit=2,
            )
            if workspace
            else []
        )
        registry = branches[0] if len(branches) == 1 else None
    while True:
        if records is not None:
            page = records
        elif registry is not None:
            children, cursor = await registry.nodes_page(
                edge=[CATALOGS], node=["Track"], cursor=cursor, limit=100
            )
            page = [{"id": child.id} for child in children]
        else:
            page = []
        locations = await locate_resources(
            "track",
            [r["id"] for r in page],
            workspace_hints=[workspace_id],
            allowed_app_states=DATA_APP_STATES,
        )
        for record in page:
            if scope.startswith("track:") and record["id"] != scope.split(":", 1)[1]:
                continue
            location = locations.get(record["id"])
            if location is None or location.workspace_id != workspace_id:
                continue
            if scope.startswith("app:") and location.app_id != scope.split(":", 1)[1]:
                continue
            if scope_inventory is not None:
                scope_inventory.add(record["id"])
            decision = await evaluate(
                subject=subject,
                action="track.read",
                resource=Resource(
                    kind="track", id=record["id"], scope=f"workspace:{workspace_id}"
                ),
            )
            if decision.allowed:
                tracks.append(record["id"])
        if len(tracks) > 5000 or (
            scope_inventory is not None and len(scope_inventory) > 5000
        ):
            raise BadRequestError(message="Narrow this search to an App or list")
        if records is not None or cursor is None:
            return tracks


async def direct_entries(
    subject, workspace_id: str, scope: str, candidate_tracks: list[str]
) -> list[str]:
    """Direct Entry grants need not grant read access to their whole Track."""
    from app.models.edges import COLLABORATES_ON, OWNS
    from app.services.permissions import get_user_node
    from app.services.resource_location import DATA_APP_STATES, locate_resources
    from app.services.share_grant_sources import share_grant_targets

    user = await get_user_node(subject.id)
    if user is None or not candidate_tracks:
        return []
    from jvspatial.core.context import get_default_context

    sourced = await share_grant_targets(user.id, "entry")
    source_records = (
        await get_default_context().database.find(
            "node",
            {
                "entity": Entry._entity_name(),
                "id": {"$in": sorted(sourced)},
                "context.track_id": {"$in": candidate_tracks},
            },
            limit=1001,
        )
        if sourced
        else []
    )
    admitted = await _eligible_scope(
        subject, workspace_id, scope, [row["id"] for row in source_records]
    )
    cursor = None
    while True:
        # Push the declared Track selector into jvspatial's neighbour page.
        # It is discovery only: fresh containment and policy still admit each
        # candidate. No scan of unrelated grants or readable-Track records.
        entries, cursor = await user.nodes_page(
            edge=[COLLABORATES_ON, OWNS],
            node=[{"Entry": {"context.track_id": {"$in": candidate_tracks}}}],
            cursor=cursor,
            limit=100,
        )
        ids = [entry.id for entry in entries if entry.id not in admitted]
        locations = await locate_resources(
            "entry",
            ids,
            workspace_hints=[workspace_id],
            allowed_app_states=DATA_APP_STATES,
        )
        for identity, location in locations.items():
            if (
                location is None
                or location.workspace_id != workspace_id
                or location.track_id not in candidate_tracks
            ):
                continue
            if scope.startswith("app:") and location.app_id != scope.split(":", 1)[1]:
                continue
            if (
                scope.startswith("track:")
                and location.track_id != scope.split(":", 1)[1]
            ):
                continue
            decision = await evaluate(
                subject=subject,
                action="entry.read",
                resource=Resource(
                    kind="entry", id=identity, scope=f"track:{location.track_id}"
                ),
            )
            if decision.allowed:
                admitted.append(identity)
        if len(admitted) > 5000:
            raise BadRequestError(message="Narrow this search to an App or list")
        if cursor is None:
            return admitted


async def retrieve_passages(
    subject,
    payload,
    *,
    driver,
    k: int,
    top_n: int,
    eligible_entry_ids: list[str] | None = None,
    type_selection: dict[str, list[str] | None] | None = None,
):
    """Return authorized current-source passages within a token budget.

    ``eligible_entry_ids`` narrows the passage selector to a caller-selected
    structured candidate set. It never widens the host-bound workspace, App,
    Track or policy scope. None applies no extra narrowing; an empty list
    selects nothing. Explicit candidates avoid unrelated Track/grant sweeps.
    """
    from app.services.agent_scope import active_workspace_id
    from app.services.resource_location import DATA_APP_STATES, locate_resource

    from .embedding_model import passage_tokenizer
    from .profile_encoder import encode_profile

    started = time.perf_counter()
    timings = {}
    mark = started

    def phase(name):
        nonlocal mark
        now = time.perf_counter()
        timings[name] = round((now - mark) * 1000, 2)
        mark = now

    scope = payload.scope or ""
    workspace_id = active_workspace_id()
    if scope.startswith("workspace:"):
        requested = scope.split(":", 1)[1]
        if workspace_id and requested != workspace_id:
            raise InsufficientPermissionsError(
                message="Search target is outside this workspace"
            )
        workspace_id = requested
    elif scope.startswith(("app:", "track:")):
        kind, identity = scope.split(":", 1)
        location = await locate_resource(
            kind, identity, allowed_app_states=DATA_APP_STATES
        )
        if location is None or (workspace_id and location.workspace_id != workspace_id):
            raise InsufficientPermissionsError(
                message="Search target is outside this workspace"
            )
        workspace_id = location.workspace_id
    if not workspace_id:
        raise InsufficientPermissionsError(
            message="Choose a workspace before searching"
        )
    phase("scope_ms")
    if type_selection is not None:
        if not type_selection:
            return [], 0, 0, payload.mode, False
        if (
            len(type_selection) > 500
            or sum(len(v or []) for v in type_selection.values()) > 500
        ):
            raise BadRequestError(message="Narrow this search to fewer record types")
    if payload.mode != "graph":
        from .embedding_configuration import encoding_authorized
        from .profile_encoder import WorkspaceEmbeddingUnavailable

        # A disabled former provider cannot encode during migration. Refuse
        # before the audience sweep, so lexical fallback does that work once.
        # This preflight sends no text; the physical encoder still rechecks
        # current egress authority after candidate admission.
        selected = await resolve_workspace_embedding_profile(workspace_id)
        if not await encoding_authorized(selected):
            raise WorkspaceEmbeddingUnavailable("The search model is not available")
    eligible = None
    if eligible_entry_ids is not None:
        eligible = list(
            dict.fromkeys(
                str(value).strip()
                for value in eligible_entry_ids[:500]
                if value and str(value).strip()
            )
        )
        if not eligible:
            return [], 0, 0, payload.mode, False
        tracks = []
        granted_entries = await _eligible_scope(subject, workspace_id, scope, eligible)
        if not granted_entries:
            return [], 0, 0, payload.mode, False
    else:
        inventory: set[str] = set()
        tracks = (
            await scoped_tracks(subject, workspace_id, scope, list(type_selection))
            if type_selection is not None
            else await scoped_tracks(
                subject, workspace_id, scope, scope_inventory=inventory
            )
        )
        granted_entries = (
            await direct_entries(
                subject, workspace_id, scope, sorted(inventory - set(tracks))
            )
            if type_selection is None and inventory != set(tracks)
            else []
        )
    phase("audience_ms")
    try:
        profile = await resolve_workspace_embedding_profile(workspace_id)
    except Exception:
        if payload.mode != "graph":
            raise
        # Text search is independent of a broken private provider reference.
        from .index_definition import embedding_profile

        try:
            profile = embedding_profile()
        except (ValueError, OSError) as exc:
            from .profile_encoder import WorkspaceEmbeddingUnavailable

            raise WorkspaceEmbeddingUnavailable(
                "The deployment search model needs a configuration update"
            ) from exc
    store = passage_store(driver, profile)
    phase("admission_ms")
    tokenizer = await passage_tokenizer()
    query_tokens = tokenizer.encode(payload.query, add_special_tokens=False)
    if len(query_tokens.ids) > 240:
        raise BadRequestError(message="Use a shorter search phrase")
    phase("tokenizer_ms")
    from .encoding_runtime import interactive_encoding

    with interactive_encoding():
        query_vec = (
            [1.0] + [0.0] * (profile.dimensions - 1)
            if payload.mode == "graph"
            else (await encode_profile([payload.query], profile))[0]
        )
    phase("encoding_ms")
    candidates = await store.search(
        query_vec,
        workspace_id=workspace_id,
        track_ids=tracks,
        k=k,
        query=payload.query,
        mode=payload.mode,
        direct_entry_ids=granted_entries,
        eligible_entry_ids=eligible,
        **({"type_selection": type_selection} if type_selection is not None else {}),
    )
    phase("ranking_ms")
    budget = payload.context_tokens
    results: dict[str, RetrievedEntry] = {}
    current_documents = {}
    from jvspatial.core.context import get_default_context

    from app.services.resource_location import locate_resources

    records: dict[str, dict[str, Any]] = {}
    locations: dict[str, Any] = {}
    attempted = set()
    schemas: dict[str, Any] = {}
    dropped = 0

    async def prepare(identity):
        """Keep ordinary per-item policy checks; overlap only bounded read I/O."""
        from app.middleware.permissions_cache import isolated_permissions_cache

        record = records.get(identity)
        entry = (
            await get_default_context()._deserialize_entity(Entry, record)
            if record and locations.get(identity)
            else None
        )
        document, denied = None, False
        if entry:
            with isolated_permissions_cache():
                decision = await evaluate(
                    subject=subject,
                    action="entry.read",
                    resource=Resource(
                        kind="entry", id=identity, scope=f"track:{entry.track_id}"
                    ),
                )
            denied = not decision.allowed
            if decision.allowed:
                document = await index_document(
                    entry, location=locations[identity], schemas=schemas
                )
                if document and type_selection is not None:
                    selected_types = type_selection.get(document["track_id"], [])
                    if (
                        selected_types is not None
                        and entry.type_id not in selected_types
                    ):
                        document = None
                if document and (
                    document["workspace_id"] != workspace_id
                    or (
                        document["track_id"] not in tracks
                        and identity not in granted_entries
                    )
                    or (
                        scope.startswith("app:")
                        and document["app_id"] != scope.split(":", 1)[1]
                    )
                ):
                    document = None
        return entry, document, denied

    for position, candidate in enumerate(candidates):
        identity = candidate["entry_id"]
        if identity not in results and len(results) >= top_n:
            continue
        if identity not in current_documents:
            page = list(
                dict.fromkeys(
                    c["entry_id"]
                    for c in candidates[position:]
                    if c["entry_id"] not in attempted
                )
            )[: max(5, min(20, top_n * 2))]
            attempted.update(page)
            locations.update(
                await locate_resources(
                    "entry",
                    page,
                    workspace_hints=[workspace_id],
                    allowed_app_states=DATA_APP_STATES,
                    records_out=records,
                )
            )
            # Four independent canonical checks at a time; never fan out over
            # the corpus or substitute cached/indexed ACLs for current policy.
            # A caller-owned transaction is task/connection affine: keep every
            # read on its owner task rather than spawning concurrent children.
            from app.services.entry_write_scope import is_postgres_entry_transaction

            serial = is_postgres_entry_transaction(get_default_context().database)
            for start in range(0, len(page), 4):
                batch = page[start : start + 4]
                prepared = (
                    [await prepare(eid) for eid in batch]
                    if serial
                    else await asyncio.gather(
                        *(prepare(eid) for eid in batch), return_exceptions=True
                    )
                )
                for eid, item in zip(batch, prepared):
                    if isinstance(item, BaseException):
                        raise item
                    entry, document, denied = item
                    current_documents[eid] = (entry, document)
                    dropped += int(denied)
        entry, document = current_documents[identity]
        if not document or document["source_hash"] != candidate["source_hash"]:
            continue
        if payload.filters:
            from app.api.retrieve import _entry_matches_filters

            if not _entry_matches_filters(entry, payload.filters):
                continue
        # Read current canonical text, not stored passage text. Offset/hash checks
        # exclude stale vectors and policy changes even before reindexing runs.
        text = document["texts"].get(candidate["field_path"], "")
        passage = text[candidate["start_offset"] : candidate["end_offset"]]
        if passage != candidate["content"]:
            continue
        if identity not in results and len(results) >= top_n:
            continue
        passage_start = candidate["start_offset"]
        tokens = tokenizer.encode(passage, add_special_tokens=False)
        if not budget:
            break
        if len(tokens.ids) > budget:
            # Preserve the part that matches the question when shortening a
            # passage; taking its prefix can cut off the actual answer.
            from .keyword_match import tokenize

            terms = tokenize(payload.query)
            positions = [
                passage.lower().find(term) for term in terms if term in passage.lower()
            ]
            anchor = min(positions) if positions else 0
            token_anchor = next(
                (i for i, (_a, b) in enumerate(tokens.offsets) if b > anchor), 0
            )
            first = max(0, min(token_anchor - budget // 4, len(tokens.ids) - budget))
            a = tokens.offsets[first][0]
            b = tokens.offsets[first + budget - 1][1]
            passage = passage[a:b]
            passage_start += a
            used = budget
        else:
            used = len(tokens.ids)
        budget -= used
        if identity not in results:
            results[identity] = RetrievedEntry(
                entry_id=identity,
                track_id=document["track_id"],
                title=document["title"],
                source_revision=document["source_revision"],
                profile_id=profile.id,
                action_url=f"/tracks/{document['track_id']}?entry={identity}",
                score=float(candidate["score"]),
                mode_origin=(
                    ["graph"]
                    if payload.mode == "graph"
                    else (["graph"] if candidate.get("lexical_match") else [])
                    + (
                        ["semantic"]
                        if candidate.get(
                            "vector_match", not candidate.get("lexical_match")
                        )
                        else []
                    )
                ),
                snippet=passage[:200],
                provenance=getattr(entry, "provenance", None),
            )
        result = results[identity]
        # Avoid repeating overlapping passages in the context sent to a model.
        if any(
            p.field == candidate["field_path"]
            and p.start < candidate["end_offset"]
            and candidate["start_offset"] < p.end
            for p in result.passages
        ):
            budget += used
            continue
        result.passages.append(
            RetrievedPassage(
                field=candidate["field_path"],
                start=passage_start,
                end=passage_start + len(passage),
                text=passage,
                tokens=used,
            )
        )
    # A matching heading is navigation, not sufficient answer evidence. Reuse
    # only the already admitted, hash-checked document's selected text, within
    # the remaining shared token budget. No extra graph/provider request, and
    # no assumption that the prefix itself matched the query.
    for identity, result in results.items():
        if budget <= 0 or any(p.field != "title" for p in result.passages):
            continue
        _, document = current_documents[identity]
        texts = document["texts"]
        field = next(
            (key for key in texts if key != "title" and texts[key].strip()), None
        )
        if field is None:
            continue
        # Tokenize a bounded prefix, never a whole arbitrarily long record.
        text = texts[field][:8192]
        encoding = tokenizer.encode(text, add_special_tokens=False)
        offsets = encoding.offsets[: min(budget, profile.chunk_tokens)]
        if not offsets:
            continue
        a, b = offsets[0][0], offsets[-1][1]
        result.passages.append(
            RetrievedPassage(
                field=field,
                start=a,
                end=b,
                text=text[a:b],
                tokens=len(offsets),
                context_only=True,
            )
        )
        result.snippet = text[a:b][:200]
        budget -= len(offsets)
    phase("validation_packing_ms")
    timings["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
    logger.info("retrieval stages: %s", timings)
    return list(results.values()), dropped, len(candidates), payload.mode, False
