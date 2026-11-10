"""Check retained native retrieval evidence before reusing a checkpoint."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from jvspatial.core.context import get_default_context
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from app.agentive.harness.contracts import HarnessExecutionScope
from app.middleware.permissions_cache import isolated_permissions_cache
from app.models.nodes import Entry
from app.schemas.policy import Resource, Subject
from app.schemas.retrieve import RetrievedEntry
from app.services.policy_engine import evaluate
from app.services.resource_location import DATA_APP_STATES, locate_resource
from app.services.retrieval.indexing import index_document
from app.services.workspace_permissions import can_access_workspace

RETRIEVAL_TOOLS = frozenset({"integral_query", "integral_search_cross_track"})
RETRIEVAL_EVIDENCE = "integral_retrieval_evidence_v1"
_MAX_RETAINED_ENTRIES = 32
_MAX_RETAINED_PASSAGES = 128


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class PassageEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    field: str = Field(min_length=1, max_length=512)
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class EntryEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    entry_id: str = Field(min_length=1, max_length=255)
    track_id: str = Field(min_length=1, max_length=255)
    revision: int = Field(ge=1, strict=True)
    title_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    passages: tuple[PassageEvidence, ...] = Field(
        min_length=1, max_length=_MAX_RETAINED_PASSAGES
    )


_EVIDENCE = TypeAdapter(list[EntryEvidence])


def retrieval_result_evidence(content: Any) -> list[dict] | None:
    """Bind clearable results to canonical offsets without retaining source text."""
    results = content.get("results") if isinstance(content, dict) else None
    if not isinstance(results, list) or len(results) > _MAX_RETAINED_ENTRIES:
        return None
    rows: list[dict] = []
    count = 0
    try:
        for raw in results:
            row = RetrievedEntry.model_validate(raw)
            count += len(row.passages)
            if (
                count > _MAX_RETAINED_PASSAGES
                or row.source_revision is None
                or not row.passages
                or any(p.attachment_id or p.content_origin for p in row.passages)
                or (
                    row.snippet
                    and not any(p.text.startswith(row.snippet) for p in row.passages)
                )
            ):
                return None
            evidence = EntryEvidence(
                entry_id=row.entry_id,
                track_id=row.track_id,
                revision=row.source_revision,
                title_digest=_digest(row.title) if row.title is not None else None,
                passages=tuple(
                    PassageEvidence(
                        field=p.field, start=p.start, end=p.end, digest=_digest(p.text)
                    )
                    for p in row.passages
                ),
            )
            rows.append(evidence.model_dump(mode="json"))
    except ValidationError:
        return None
    return rows


def retained_retrieval_evidence(
    history: list[Any], *, allow_unproven_tool_results: bool = False
) -> list[EntryEvidence] | None:
    """Collect receipts and carried summary evidence. None means unprovable."""
    from app.agentive.harness.pydantic_ai_compat import ModelRequest, ToolReturnPart

    rows: dict[EntryEvidence, None] = {}
    count = 0
    for message in history:
        metadata = getattr(message, "metadata", None) or {}
        proofs = (
            [metadata[RETRIEVAL_EVIDENCE]] if RETRIEVAL_EVIDENCE in metadata else []
        )
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if (
                    not isinstance(part, ToolReturnPart)
                    or part.tool_name not in RETRIEVAL_TOOLS
                ):
                    continue
                metadata = part.metadata if isinstance(part.metadata, dict) else {}
                if isinstance(part.content, dict):
                    raw = retrieval_result_evidence(part.content)
                    if (
                        RETRIEVAL_EVIDENCE in metadata
                        and raw != metadata[RETRIEVAL_EVIDENCE]
                    ):
                        return None
                    if raw is not None or not allow_unproven_tool_results:
                        proofs.append(raw)
                else:
                    proof = metadata.get(RETRIEVAL_EVIDENCE)
                    if proof is not None or not allow_unproven_tool_results:
                        proofs.append(proof)
        for proof in proofs:
            if not isinstance(proof, list) or len(proof) > _MAX_RETAINED_ENTRIES:
                return None
            try:
                batch = _EVIDENCE.validate_python(proof)
            except ValidationError:
                return None
            for row in batch:
                if row not in rows:
                    rows[row] = None
                    count += len(row.passages)
                    if (
                        len(rows) > _MAX_RETAINED_ENTRIES
                        or count > _MAX_RETAINED_PASSAGES
                    ):
                        return None
    return list(rows)


async def retained_retrieval_is_current(
    history: list[Any],
    *,
    principal_id: str,
    workspace_id: str,
    allow_unproven_tool_results: bool = False,
) -> bool:
    """Validate retained evidence against current canonical text and access."""
    rows = retained_retrieval_evidence(
        history, allow_unproven_tool_results=allow_unproven_tool_results
    )
    return await _evidence_is_current(
        rows, principal_id=principal_id, workspace_id=workspace_id
    )


async def _evidence_is_current(
    rows: list[EntryEvidence] | None, *, principal_id: str, workspace_id: str
) -> bool:
    if rows is None:
        return False
    if not rows:
        return True
    with isolated_permissions_cache():
        if await can_access_workspace(principal_id, workspace_id) == "none":
            return False
    subject = Subject(kind="human", id=principal_id)
    graph = get_default_context()
    documents: dict[str, dict] = {}
    for row in rows:
        document = documents.get(row.entry_id)
        if document is None:
            records: dict[str, dict] = {}
            location = await locate_resource(
                "entry",
                row.entry_id,
                allowed_app_states=DATA_APP_STATES,
                records_out=records,
            )
            record = records.get(row.entry_id)
            if location is None or location.workspace_id != workspace_id or not record:
                return False
            with isolated_permissions_cache():
                decision = await evaluate(
                    subject=subject,
                    action="entry.read",
                    resource=Resource(
                        kind="entry",
                        id=row.entry_id,
                        scope=f"track:{location.track_id}",
                    ),
                    _dry_run=True,
                )
            if not decision.allowed:
                return False
            entry = await graph._deserialize_entity(Entry, record)
            document = await index_document(entry, location=location) if entry else None
            if not document:
                return False
            documents[row.entry_id] = document
        if (
            document["track_id"] != row.track_id
            or document["source_revision"] != row.revision
            or (
                row.title_digest is not None
                and _digest(document["title"]) != row.title_digest
            )
        ):
            return False
        for passage in row.passages:
            text = document["texts"].get(passage.field)
            if (
                not isinstance(text, str)
                or not 0 <= passage.start < passage.end <= len(text)
                or _digest(text[passage.start : passage.end]) != passage.digest
            ):
                return False
    return True


class RetrievalEvidenceStale(RuntimeError):
    """A model request would reuse source evidence that is no longer current."""


async def assert_retrieval_history_current(
    history: list[Any],
    *,
    principal_id: str,
    workspace_id: str,
    allow_unproven_tool_results: bool = False,
) -> None:
    """Reject requests containing retained retrieval evidence that cannot be reused."""
    if not await retained_retrieval_is_current(
        history,
        principal_id=principal_id,
        workspace_id=workspace_id,
        allow_unproven_tool_results=allow_unproven_tool_results,
    ):
        raise RetrievalEvidenceStale("Prior retrieval evidence changed")


_request_evidence: ContextVar[
    tuple[HarnessExecutionScope, list[EntryEvidence]] | None
] = ContextVar("model_request_retrieval_evidence", default=None)


@contextmanager
def bind_model_request_retrieval(
    scope: HarnessExecutionScope,
    history: list[Any],
    *,
    allow_unproven_tool_results: bool = False,
):
    """Hold private source receipts across the native request and SDK retries."""
    rows = retained_retrieval_evidence(
        history, allow_unproven_tool_results=allow_unproven_tool_results
    )
    if rows is None:
        raise RetrievalEvidenceStale("Prior retrieval evidence cannot be verified")
    parent = _request_evidence.get()
    if parent is not None:
        parent_scope, parent_rows = parent
        if parent_scope != scope:
            raise RetrievalEvidenceStale("Retrieval request scope changed")
        rows = list(dict.fromkeys([*parent_rows, *rows]))
        if (
            len(rows) > _MAX_RETAINED_ENTRIES
            or sum(len(row.passages) for row in rows) > _MAX_RETAINED_PASSAGES
        ):
            raise RetrievalEvidenceStale("Prior retrieval evidence cannot be verified")
    token = _request_evidence.set((scope, rows))
    try:
        yield
    finally:
        _request_evidence.reset(token)


async def assert_bound_model_retrieval_current(scope: HarnessExecutionScope) -> None:
    """Check the bound source proof immediately before a physical attempt."""
    bound = _request_evidence.get()
    if bound is None:
        return
    captured_scope, rows = bound
    if captured_scope != scope or not await _evidence_is_current(
        rows, principal_id=scope.principal_id, workspace_id=scope.workspace_id
    ):
        raise RetrievalEvidenceStale("Prior retrieval evidence changed")
