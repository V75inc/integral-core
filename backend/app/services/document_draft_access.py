"""Scoped, revision-bound source snapshots for document delivery and review."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from app.api.errors import BadRequestError, ResourceConflictError, ResourceNotFoundError


@dataclass(frozen=True)
class DocumentDraftSnapshot:
    """Immutable source identity; body and metadata must not appear in logs."""

    user_id: str
    workspace_id: str
    thread_id: str
    key: str
    version: int
    title: str = field(repr=False)
    body: str = field(repr=False)
    format: str = ""
    metadata_json: str = field(default="", repr=False)
    source_digest: str = ""


async def load_document_draft_snapshot(
    *, user_id: str, workspace_id: str, thread_id: str, key: str, version: int
) -> DocumentDraftSnapshot:
    """Read a current private draft with its host-authorized scope and revision."""
    from app.agentive.artifacts import get_artifact

    if not key or len(key) > 128 or key != key.strip() or version < 1:
        raise BadRequestError(message="A draft key and revision are required")
    draft = await get_artifact(
        user_id=user_id, workspace_id=workspace_id, session_id=thread_id, key=key
    )
    if not draft.get("ok") or draft.get("kind") != "document_draft":
        raise ResourceNotFoundError(message="Document draft not found")
    if draft.get("version") != version:
        raise ResourceConflictError(
            message="This draft has changed. Open the latest version."
        )
    metadata = draft.get("metadata") or {}
    metadata_json = json.dumps(
        metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    title = str(draft.get("title") or "")
    body = str(draft.get("body") or "")
    source = json.dumps(
        [user_id, workspace_id, thread_id, key, version, title, body, metadata_json],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return DocumentDraftSnapshot(
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
        key=key,
        version=version,
        title=title,
        body=body,
        format=str(metadata.get("format") or ""),
        metadata_json=metadata_json,
        source_digest=hashlib.sha256(source).hexdigest(),
    )


async def require_current_document_draft(snapshot: DocumentDraftSnapshot) -> None:
    """Recheck live access and exact reviewed source under the caller's admission."""
    current = await load_document_draft_snapshot(
        user_id=snapshot.user_id,
        workspace_id=snapshot.workspace_id,
        thread_id=snapshot.thread_id,
        key=snapshot.key,
        version=snapshot.version,
    )
    if current != snapshot:
        raise ResourceConflictError(
            message="This draft has changed. Open the latest version."
        )
