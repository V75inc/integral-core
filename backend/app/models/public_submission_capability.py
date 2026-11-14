"""Core-owned bearer capabilities for mutating one anonymous submission."""

from __future__ import annotations

from typing import ClassVar, Optional

from jvspatial.core import Object

from app.models.indexing import attribute, compound_index


@compound_index(
    [("capability_hash", 1)],
    name="public_submission_capability_hash",
    unique=True,
    partial_filter_expression={
        "entity": "PublicSubmissionCapability",
        "context.capability_hash": {"$gt": ""},
    },
)
@compound_index(
    [("submission_id", 1), ("status", 1)],
    name="public_submission_capability_submission",
)
@compound_index(
    [("entry_id", 1), ("status", 1)],
    name="public_submission_capability_entry",
)
class PublicSubmissionCapability(Object):
    """Hash-only, expiring and revocable authority for one public submission."""

    __entity_name__: ClassVar[Optional[str]] = "PublicSubmissionCapability"

    capability_hash: str = attribute(default="", indexed=True)
    submission_id: str = attribute(default="", indexed=True)
    entry_id: str = attribute(default="", indexed=True)
    share_link_id: str = attribute(default="", indexed=True)
    workspace_id: str = attribute(default="", indexed=True)
    track_id: str = attribute(default="", indexed=True)
    form_id: str = attribute(default="", indexed=True)
    status: str = attribute(default="active", indexed=True)
    expires_at: str = attribute(default="", indexed=True)
    revoked_at: Optional[str] = attribute(default=None)
    created_at: str = ""
    updated_at: str = ""
