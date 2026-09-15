"""SyncConnector ABC + handoff types.

Locked decision #1 (provenance shape — split): synced Entries get
``provenance.source = "connector"`` (ActorKind member) + ``provenance.source_id
= "<connector_id>:<external_id>"`` (free string). NEVER assign to
``provenance.source`` any free-string that begins with the ``connector`` token
followed by a colon — that fails the Pydantic ActorKind boundary because
``ActorKind = Literal["human", "agent", "connector", "system"]`` and the free
form is not a Literal member. See I-CON-01 grep gate.

Locked decision #6 (idempotency-key namespace): the default
``idempotency_key_for`` hashes ``(slug, external_id)`` together. Per-Connector
``connector_id`` is the per-instance namespace; ``slug`` is the per-CLASS
namespace. Both together prevent the collision pattern in RESEARCH Pitfall 4
where two connectors against the same external service produce the same key.

Locked decision #12 (core, not AGENTIVE_ENABLED-gated): only the BASE class +
registry live here. Subclasses live in ``app/agentive/connectors/<slug>.py``
(the Connector Node itself stays in agentive per Phase 1 D-08).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import (
    Any,
    AsyncIterator,
    Dict,
    List,
    Literal,
    Optional,
)

ConflictPolicy = Literal["last_write_wins", "manual_resolve", "mirror_only"]


@dataclass
class ExternalRecord:
    """Vendor-neutral envelope around one external entity.

    The payload is deliberately untyped — connector subclasses define the schema
    in their seeded ContentProfile and project the payload via ``to_entry``.
    External systems may add fields ad hoc; we do not validate the payload
    shape at the core boundary.
    """

    external_id: str
    payload: Dict[str, Any] = field(default_factory=dict)
    updated_at: Optional[str] = (
        None  # external-system ISO timestamp — drives conflict detection
    )


@dataclass
class MaterializedEntry:
    """Connector → core handoff.

    Connector ``to_entry`` returns this; the core sync_runtime (Plan 05-03)
    writes the Entry node with the right provenance and idempotency key.
    """

    title: str
    body: str
    entry_type_key: str  # references the seeded ContentProfile manifest
    tags: List[str] = field(default_factory=list)
    custom_fields: Dict[str, Any] = field(default_factory=dict)
    external_updated_at: Optional[str] = None


class SyncConnector:
    """Implement for each external system (GitHub, RSS, local FS, ...).

    Subclasses register via ``@register_sync_connector("<slug>")`` in
    ``app/agentive/connectors/<slug>.py`` (the subclass lives in agentive per
    locked decision #12; only the BASE class lives in core).
    """

    slug: str = ""
    conflict_policy: ConflictPolicy = "last_write_wins"

    async def sync_pull(self, *, connector: Any) -> AsyncIterator[ExternalRecord]:  # type: ignore[empty-body]
        """Yield external records to materialize.

        Connector subclass calls the external API and yields records. Cursor
        advance is the core's responsibility (sync_runtime in Plan 05-03
        updates ``connector.sync_cursor``).

        Note: ``connector`` parameter is typed ``Any`` to avoid an import cycle
        on the agentive-resident Connector Node; sync_runtime types it
        concretely at the call site.
        """
        raise NotImplementedError
        yield  # pragma: no cover — marks the function as an async generator

    def idempotency_key_for(self, record: ExternalRecord) -> str:
        """Default: SHA-256 of ``slug:external_id``.

        Pitfall 4 (RESEARCH §"Common Pitfalls"): external systems do not
        coordinate ID namespaces — two connectors might both produce
        ``external_id="123"``. The slug prefix prevents cross-connector key
        collisions. Subclasses MAY override for systems where ``external_id``
        alone isn't deterministic per logical record (e.g. RSS feeds that
        change item GUIDs across regenerations).
        """
        return hashlib.sha256(f"{self.slug}:{record.external_id}".encode()).hexdigest()

    def to_entry(self, record: ExternalRecord) -> MaterializedEntry:
        """Map one external record → an Entry-shaped payload. Subclass-specific."""
        raise NotImplementedError
