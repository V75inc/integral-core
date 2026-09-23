"""Public information-contract shapes for Integral App authors.

These are dependency-light transport annotations.  Core owns validation and
resolution; Apps use the shapes to declare and exchange stable field identity
and optimistic-concurrency inputs without importing Core internals.
"""

from __future__ import annotations

from typing import Literal, TypedDict

FieldNamespace = Literal["platform", "business", "system"]
RelationTarget = Literal["entry", "track"]
TargetRemovalBehavior = Literal["block", "null", "archive_self"]


class RelationDefinition(TypedDict, total=False):
    """Graph behavior declared by a relation field."""

    target: RelationTarget
    many: bool
    allow_cross_track: bool
    on_target_removal: TargetRemovalBehavior


class FieldDefinition(TypedDict, total=False):
    """A stable, versioned field declaration returned by Core."""

    id: str
    key: str
    label: str
    type: str
    namespace: FieldNamespace
    owner: str
    schema_revision: int
    relation: RelationDefinition


class RecordRevision(TypedDict):
    """Optimistic-concurrency values supplied with a record write."""

    record_revision: int
    schema_revision: int


__all__ = [
    "FieldDefinition",
    "FieldNamespace",
    "RecordRevision",
    "RelationDefinition",
    "RelationTarget",
    "TargetRemovalBehavior",
]
