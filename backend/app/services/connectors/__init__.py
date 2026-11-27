"""CON-02 — sync-connector authoring contract (core).

Lives in core so sync runs with the always-on agentive layer. There is no
AGENTIVE_ENABLED boot gate.

Public surface:

- ``SyncConnector`` — abstract base class for pull-based external-system
  connectors. Subclasses implement ``sync_pull`` (async generator yielding
  ``ExternalRecord`` instances) and ``to_entry`` (projection to
  ``MaterializedEntry``).
- ``ExternalRecord`` / ``MaterializedEntry`` — vendor-neutral envelopes that
  cross the connector → core boundary.
- ``ConflictPolicy`` — 3-value Literal (``last_write_wins`` | ``manual_resolve``
  | ``mirror_only``) declaring how the sync runtime resolves a re-pulled record
  whose ``external_updated_at`` is newer than the stored Entry's.
- ``register_sync_connector`` / ``get_sync_connector`` / ``reset_sync_registry``
  / ``list_registered_slugs`` — decorator-style sync registry. Chat-agent
  dispatch is a separate registry and is not a connector package.

Locked decisions: #1 (provenance split), #6 (idempotency key default), #9
(IS_CONNECTED_TO edge), #11 (decorator registry), #12 (core, not agentive).
"""

from .base import (
    ConflictPolicy,
    ExternalRecord,
    MaterializedEntry,
    SyncConnector,
)
from .registry import (
    get_sync_connector,
    list_registered_slugs,
    register_sync_connector,
    reset_sync_registry,
)

__all__ = [
    "SyncConnector",
    "ExternalRecord",
    "MaterializedEntry",
    "ConflictPolicy",
    "register_sync_connector",
    "get_sync_connector",
    "reset_sync_registry",
    "list_registered_slugs",
]
