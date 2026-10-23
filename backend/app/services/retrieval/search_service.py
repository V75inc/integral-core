"""Permission-bound passage retrieval shared by REST and the agent surface."""

from __future__ import annotations

from typing import Any

from app.api.errors import BadRequestError, InsufficientPermissionsError
from app.models.nodes import Entry
from app.schemas.policy import Resource, Subject
from app.schemas.retrieve import RetrievedEntry, RetrievedPassage
from app.services.policy_engine import evaluate

from .embedding_configuration import resolve_workspace_embedding_profile
from .indexing import index_document
from .passage_store import passage_store


async def scoped_tracks(subject: Subject, workspace_id: str, scope: str) -> list[str]:
    """Resolve current readable Tracks inside the exact host-bound scope."""
    from jvspatial.core.context import get_default_context

    from app.services.resource_location import DATA_APP_STATES, locate_resources

    database = get_default_context().database
    after = ""
    tracks = []
    if scope.startswith("track:"):
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
    while True:
        page = (
            records
            if records is not None
            else await database.find(
                "node",
                {
                    "entity": "Track",
                    "context.workspace_id": workspace_id,
                    "id": {"$gt": after},
                },
                sort=[("id", 1)],
                limit=100,
            )
        )
        locations = await locate_resources(
            "track",
            [r["id"] for r in page],
            workspace_hints=[workspace_id],
            allowed_app_states=DATA_APP_STATES,
        )
        for record in page:
            location = locations.get(record["id"])
            if location is None or location.workspace_id != workspace_id:
                continue
            if scope.startswith("app:") and location.app_id != scope.split(":", 1)[1]:
                continue
            decision = await evaluate(
                subject=subject,
                action="track.read",
                resource=Resource(
                    kind="track", id=record["id"], scope=f"workspace:{workspace_id}"
                ),
            )
            if decision.allowed:
                tracks.append(record["id"])
        if records is not None or len(page) < 100:
            return tracks
        after = page[-1]["id"]
        if len(tracks) > 5000:
            raise BadRequestError(message="Narrow this search to an App or list")


async def direct_entries(subject, workspace_id: str, scope: str) -> list[str]:
    """Direct Entry grants need not grant read access to their whole Track."""
    from jvspatial.core.context import get_default_context

    from app.models.edges import COLLABORATES_ON, OWNS
    from app.services.permissions import get_user_node
    from app.services.resource_location import DATA_APP_STATES, locate_resources

    user = await get_user_node(subject.id)
    if user is None:
        return []
    database = get_default_context().database
    after, admitted = "", []
    while True:
        edges = await database.find(
            "edge",
            {
                "source": user.id,
                "entity": {
                    "$in": [COLLABORATES_ON._entity_name(), OWNS._entity_name()]
                },
                "id": {"$gt": after},
            },
            sort=[("id", 1)],
            limit=100,
        )
        ids = list(
            dict.fromkeys(
                e["target"] for e in edges if e["target"].startswith("n.Entry.")
            )
        )
        locations = await locate_resources(
            "entry",
            ids,
            workspace_hints=[workspace_id],
            allowed_app_states=DATA_APP_STATES,
        )
        for identity, location in locations.items():
            if location is None or location.workspace_id != workspace_id:
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
        if len(edges) < 100:
            return admitted
        after = edges[-1]["id"]


async def retrieve_passages(
    subject,
    payload,
    *,
    driver,
    k: int,
    top_n: int,
    eligible_entry_ids: list[str] | None = None,
):
    """Return authorized current-source passages within a token budget.

    ``eligible_entry_ids`` narrows the passage selector to a caller-selected
    structured candidate set. It never widens the host-bound workspace, App,
    Track or policy scope; an empty list means no extra narrowing.
    """
    from app.services.agent_scope import active_workspace_id
    from app.services.resource_location import DATA_APP_STATES, locate_resource

    from .embedding_model import passage_tokenizer
    from .profile_encoder import encode_profile

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
    tracks = await scoped_tracks(subject, workspace_id, scope)
    granted_entries = await direct_entries(subject, workspace_id, scope)
    try:
        profile = await resolve_workspace_embedding_profile(workspace_id)
    except ValueError:
        from .embedding_store import EmbeddingStoreUnavailable

        raise EmbeddingStoreUnavailable(
            "The search profile needs a configuration update"
        ) from None
    store = passage_store(driver, profile)
    tokenizer = await passage_tokenizer()
    query_tokens = tokenizer.encode(payload.query, add_special_tokens=False)
    if len(query_tokens.ids) > 240:
        raise BadRequestError(message="Use a shorter search phrase")
    query_vec = (
        [1.0] + [0.0] * (profile.dimensions - 1)
        if payload.mode == "graph"
        else (await encode_profile([payload.query], profile))[0]
    )
    eligible = [
        entry_id
        for entry_id in dict.fromkeys(
            str(value or "").strip() for value in (eligible_entry_ids or [])
        )
        if entry_id
    ][:500]
    candidates = await store.search(
        query_vec,
        workspace_id=workspace_id,
        track_ids=tracks,
        k=k,
        query=payload.query,
        mode=payload.mode,
        direct_entry_ids=granted_entries,
        eligible_entry_ids=eligible,
    )
    budget = payload.context_tokens
    results: dict[str, RetrievedEntry] = {}
    current_documents = {}
    from jvspatial.core.context import get_default_context

    from app.services.resource_location import locate_resources

    records: dict[str, dict[str, Any]] = {}
    locations = await locate_resources(
        "entry",
        list(dict.fromkeys(c["entry_id"] for c in candidates)),
        workspace_hints=[workspace_id],
        allowed_app_states=DATA_APP_STATES,
        records_out=records,
    )
    schemas: dict[str, Any] = {}
    dropped = 0
    for candidate in candidates:
        identity = candidate["entry_id"]
        if identity not in results and len(results) >= top_n:
            continue
        if identity not in current_documents:
            record = records.get(identity)
            entry = (
                await get_default_context()._deserialize_entity(Entry, record)
                if record and locations.get(identity)
                else None
            )
            document = None
            if entry:
                # Authorize before composing, exposing titles, text or metadata.
                decision = await evaluate(
                    subject=subject,
                    action="entry.read",
                    resource=Resource(
                        kind="entry", id=identity, scope=f"track:{entry.track_id}"
                    ),
                )
                if decision.allowed:
                    document = await index_document(
                        entry, location=locations[identity], schemas=schemas
                    )
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
                else:
                    dropped += 1
            current_documents[identity] = (entry, document)
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
                    else (
                        ["graph", "semantic"]
                        if payload.mode == "hybrid" and candidate["lexical_match"]
                        else ["semantic"]
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
    return list(results.values()), dropped, len(candidates), payload.mode, False
