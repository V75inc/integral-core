"""Connector Node CRUD service — per CON-01 + D-07 + D-10.

Phase 1 stores Connector Node + Owns edge (User → Connector).
Sync semantics, IS_CONNECTED_TO (Connector → Track/App), and capability
enforcement are deferred to Phase 5 / Phase 6 per CONTEXT.md ``<deferred>``.

W4 / AGT-04 baseline: this module also exposes ``update_sync_cursor`` and
``get_sync_cursor`` helpers so Phase 5 sync orchestration has a stable API to
build on. Phase 1 stores cursor strings only — no fetch/advance loops.

This service intentionally lives in ``app/agentive/services/`` (not in the
core ``app/services/``) so it loads conditionally with the agentive layer
(D-08): ``Connector`` itself is registered only inside the
``AGENTIVE_ENABLED`` block in ``app/main.py``.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional  # noqa: F401 — Any used in annotation string

from app.agentive.nodes import Connector
from app.agentive.types import AgentType
from app.schemas.agentive.connectors import (
    contains_redaction_marker,
    is_auth_state_secret_key,
)
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

# AES-256-GCM domain separator, bound as GCM associated data so a ciphertext
# minted for another subsystem (model credentials) cannot be replayed into a
# connector row. It is deliberately NOT the connector id: ``auth_state`` dicts
# are handed around detached from their node (``list_remote_tools(auth_state)``,
# ``probe_mcp_health(auth_state)``), so the decrypting side does not always
# know which connector it came from.
_AUTH_STATE_AAD = "connector.auth_state"


def _encrypt_value(value: Any) -> Any:
    from app.services.credential_crypto import (
        CIPHER_PREFIX_V1,
        encrypt_secret_for_storage,
    )

    def _encrypt_secret(raw: Any) -> Any:
        # Idempotent: callers routinely re-save a dict that already holds
        # ciphertext, and double-encrypting would make it undecryptable
        # (one decrypt pass would return the inner ``v1:`` blob).
        if isinstance(raw, str) and raw and not raw.startswith(CIPHER_PREFIX_V1):
            return encrypt_secret_for_storage(raw, aad=_AUTH_STATE_AAD)
        return _encrypt_value(raw)

    if isinstance(value, dict):
        return {
            k: (
                _encrypt_value(v)
                if not is_auth_state_secret_key(k)
                else _encrypt_secret(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_encrypt_value(v) for v in value]
    return value


def _decrypt_value(value: Any) -> Any:
    from app.services.credential_crypto import decrypt_secret_from_storage

    if isinstance(value, dict):
        return {
            k: (
                decrypt_secret_from_storage(v, aad=_AUTH_STATE_AAD)
                if is_auth_state_secret_key(k) and isinstance(v, str) and v
                else _decrypt_value(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_decrypt_value(v) for v in value]
    return value


def encrypt_auth_state(auth_state: Optional[dict]) -> dict:
    """Encrypt every credential-bearing value in ``auth_state`` at rest (F-7).

    Which keys count is the same predicate the wire redactor uses
    (``is_auth_state_secret_key``), applied recursively — so
    ``oauth.tokens.refresh_token`` and ``env.QUICKBOOKS_CLIENT_SECRET`` are
    covered, not just the top level.

    Degrades to plaintext (with a warning) when no encryption key is
    configured. A deployment without ``INTEGRAL_CREDENTIAL_ENC_KEY`` is no
    worse off than before this function existed; raising instead would take
    every connector mount down on key-less installs.
    ``decrypt_auth_state`` passes non-``v1:`` values through unchanged, so
    mixed rows (pre-existing plaintext, newly encrypted) both read back.
    """
    from app.services.credential_crypto import encryption_available

    if not auth_state:
        return dict(auth_state or {})
    if not encryption_available():
        logger.warning(
            "connector_registry_node: INTEGRAL_CREDENTIAL_ENC_KEY unavailable — "
            "connector auth_state secrets stored in plaintext"
        )
        return dict(auth_state)
    return _encrypt_value(dict(auth_state))


def decrypt_auth_state(auth_state: Optional[dict]) -> dict:
    """Inverse of :func:`encrypt_auth_state`; plaintext values pass through."""
    if not auth_state:
        return dict(auth_state or {})
    return _decrypt_value(dict(auth_state))


def encryption_applies(
    kind: Optional[str], subclass_slug: Optional[str] = None
) -> bool:
    """Whether ``auth_state`` for this connector is encrypted at rest.

    MCP connectors only, for now. Every reader of an MCP ``auth_state``
    (``mcp_client``, ``mcp_adapter``, this API surface) decrypts on the way
    out; the sync connectors — ``app/agentive/connectors/gmail.py`` and
    ``quickbooks.py`` — read ``auth_state["access_token"]`` straight off the
    node and are outside this change's blast radius, so encrypting theirs
    would break sync rather than harden it. Widening this predicate is a
    follow-up that must land together with decrypt calls in those two files.
    """
    return kind == "mcp" or (subclass_slug or "") == "mcp"


async def attach_connector_to_workspace(
    connector: Connector, workspace_id: str
) -> bool:
    """Wire ``Workspace —HAS_CONNECTOR→ Connector`` (ADR-009 §4, I-GRAPH-01).

    Idempotent — a second call on an already-attached connector is a no-op.
    Also refreshes the denormalized ``Connector.workspace_id`` cache so the
    scalar and the edge cannot disagree.
    """
    workspace_id = (workspace_id or "").strip()
    if not workspace_id:
        return False
    from app.models.edges import HAS_CONNECTOR
    from app.models.nodes import Workspace

    workspace = await Workspace.get(workspace_id)
    if workspace is None:
        logger.warning(
            "connector_registry_node: workspace %s not found; "
            "Connector %s left without a HAS_CONNECTOR edge",
            workspace_id,
            connector.id,
        )
        return False
    ctx = await workspace.get_context()
    existing = await ctx.find_edges_between(
        workspace.id, connector.id, edge_class=HAS_CONNECTOR
    )
    if not existing:
        await workspace.connect(connector, edge=HAS_CONNECTOR, mounted_at=utc_now_iso())
    if getattr(connector, "workspace_id", "") != workspace_id:
        connector.workspace_id = workspace_id
        await connector.save()
    return True


async def create_connector(
    *,
    owner: str,
    kind: AgentType = "jvagent",
    auth_state: Optional[dict] = None,
    sync_cursor: Optional[str] = None,
    mapping_profile: Optional[str] = None,
    permissions: Optional[List[str]] = None,
    capabilities: Optional[List[str]] = None,
    skip_default_policy: bool = False,
    workspace_id: Optional[str] = None,
) -> Connector:
    """Create a Connector Node + Owns edge (User → Connector).

    ``owner`` is the User.id. The ``Connector.owner`` scalar field is set for
    back-compat per D-07; the canonical relationship is the Owns edge wired
    here via the verified ``user.connect(...)`` idiom (analog:
    ``backend/app/agentive/services/agent_tools.py:798`` and ``:902`` for
    Track / App ownership).

    Edge wiring is fail-CLOSED (I-GRAPH-01): if ``User.get(owner)`` returns
    ``None`` or the connect call raises, the half-built Connector is deleted
    and the error propagates. It used to log a warning and return a DETACHED
    node — invisible to walkers, to cascade-delete and to graph backup, and
    still perfectly usable through the ``owner`` scalar, so nothing surfaced
    the breakage.

    ``workspace_id`` additionally wires ``Workspace —HAS_CONNECTOR→ Connector``
    (ADR-009 §4) in the same unit of work.

    ``skip_default_policy`` — when True, skip sync-baseline Policy
    materialization (ADR-009 MCP mount materializes ``tool.invoke`` /
    ``connector.read`` itself).
    """
    now = utc_now_iso()
    stored_auth = dict(auth_state or {})
    if encryption_applies(kind):
        stored_auth = encrypt_auth_state(stored_auth)
    c = await Connector.create(
        kind=kind,
        owner=owner,
        auth_state=stored_auth,
        sync_cursor=sync_cursor,
        mapping_profile=mapping_profile,
        permissions=permissions or [],
        capabilities=capabilities or [],
        created_at=now,
        updated_at=now,
    )
    # Establish the canonical Owns edge using the verified project idiom.
    # Analog: backend/app/agentive/services/agent_tools.py:798 (Track) / :902 (App).
    try:
        from app.agentive.edges import OWNS  # ALL_CAPS alias per S-5 / D-07
        from app.services.permissions import get_user_node

        # ``owner`` is the principal id, which is the *AuthUser* id — not the
        # ``User`` graph node id. The old ``User.get(owner)`` therefore
        # returned ``None`` for every real caller, logged a warning nobody
        # read, and shipped a detached Connector. ``get_user_node`` is the
        # canonical resolver for either id shape.
        user = await get_user_node(owner)
        if user is None:
            raise ValueError(
                f"owner User node not found for {owner!r} — refusing to persist "
                "a Connector with no Owns edge (I-GRAPH-01)"
            )
        await user.connect(c, edge=OWNS, role="owner", granted_at=now)
        if workspace_id:
            await attach_connector_to_workspace(c, workspace_id)
    except Exception:
        # I-GRAPH-01: a Connector that never got its structural edge is a
        # detached node. Roll the create back rather than leaving one behind.
        try:
            await c.delete()
        except Exception:  # noqa: BLE001 — the wiring failure is the real error
            logger.exception(
                "connector_registry_node: rollback delete failed for %s", c.id
            )
        raise
    # Phase 5 Plan 05-01 — per-connector Policy materialization (I-CON-04).
    # Without this, ``policy_engine.evaluate(Subject(kind="connector"), ...)``
    # would always return ``fail_closed_no_policy`` (Phase 3 D-04 default) and
    # the Plan 05-03 sync runtime could never write Entries on the connector's
    # behalf. Locked decision §Q8 — baseline actions are connector.sync,
    # entry.create, entry.update. ADR-009 MCP mounts skip this and call
    # materialize_mcp_policies instead.
    if not skip_default_policy:
        try:
            await materialize_policies_for_connector(connector=c, actor_id=owner)
        except Exception as e:  # noqa: BLE001 — fail-loud, mirror Owns-edge pattern
            logger.warning(
                "connector_registry_node: per-connector Policy materialization "
                "failed for connector=%s owner=%s: %s",
                c.id,
                owner,
                e,
            )
    return c


async def materialize_policies_for_connector(
    *,
    connector: Connector,
    actor_id: str,
) -> "Any":
    """Phase 5 Plan 05-01 — write the default per-connector Policy.

    Mirrors Phase 3 03-03 ``materialize_governance_policies_for_content_profile``
    precedent. Without an attached Policy, ``policy_engine.evaluate(
    Subject(kind="connector", ...))`` returns ``fail_closed_no_policy`` — the
    sync runtime would never get past the gate (I-CON-04).

    Sync connectors get ``connector.sync`` / ``entry.create`` /
    ``entry.update``. MCP connectors (``subclass_slug="mcp"``, ADR-009) get
    ``tool.invoke`` / ``connector.read`` instead.
    """
    from app.services.policy_registry import create_policy

    subclass = getattr(connector, "subclass_slug", "") or ""
    if subclass == "mcp" or getattr(connector, "kind", None) == "mcp":
        actions = ["tool.invoke", "connector.read"]
    else:
        actions = ["connector.sync", "entry.create", "entry.update"]

    policy = await create_policy(
        subject_kind="connector",
        subject_id=connector.id,
        scope=f"connector:{connector.id}",
        actions=actions,
        entry_types=[],  # all entry types allowed; per-track scope set at sync time
        tags=[],  # all tags allowed
        requires_human_approval=False,
        is_active=True,
        created_by=actor_id,
    )
    return policy


async def get_connector(connector_id: str) -> Optional[Connector]:
    """Fetch a Connector by id. Returns ``None`` if not found."""
    return await Connector.get(connector_id)


async def update_sync_cursor(connector_id: str, cursor: str) -> None:
    """Persist a sync cursor on the Connector (W4 / AGT-04 baseline).

    Phase 1 stores ONLY — no sync semantics. Real sync orchestration (read
    cursor, fetch deltas, advance cursor) lands in Phase 5 per CONTEXT D-07.
    Unknown ``connector_id`` is a no-op (does not raise) so callers don't
    have to special-case "not found" during transient Phase-5 race conditions.
    """
    c = await Connector.get(connector_id)
    if c is None:
        return
    c.sync_cursor = cursor
    c.updated_at = utc_now_iso()
    await c.save()


async def get_sync_cursor(connector_id: str) -> Optional[str]:
    """Read a sync cursor from the Connector (W4 / AGT-04 baseline).

    Returns ``None`` when the connector has no recorded cursor or does not exist.
    """
    c = await Connector.get(connector_id)
    if c is None:
        return None
    return c.sync_cursor


async def update_connector(
    connector_id: str,
    *,
    subclass_slug: Optional[str] = None,
    sync_interval_seconds: Optional[int] = None,
    auth_state: Optional[dict] = None,
    mapping_profile: Optional[str] = None,
    permissions: Optional[List[str]] = None,
    capabilities: Optional[List[str]] = None,
) -> Optional[Connector]:
    """Partial-update a Connector. ``None`` values are skipped (no overwrite-with-None).

    Phase 8 Plan 08-02 Task 1 helper. ``kind`` and ``owner`` are intentionally
    not patchable here — both are immutable per CON-01 (re-target by delete +
    recreate). Returns ``None`` when the connector is not found.
    """
    c = await Connector.get(connector_id)
    if c is None:
        return None
    if subclass_slug is not None:
        c.subclass_slug = subclass_slug
    if sync_interval_seconds is not None:
        c.sync_interval_seconds = sync_interval_seconds
    if auth_state is not None:
        # Refuse a round-tripped redacted read. GET returns credentials as
        # "[redacted]"; this PATCH REPLACES auth_state wholesale, so accepting
        # the marker back would overwrite a live token with the placeholder
        # and report success. No caller ever legitimately writes it.
        if contains_redaction_marker(auth_state):
            raise ValueError(
                "auth_state contains redacted placeholder values; omit "
                "auth_state to leave stored credentials untouched, or send "
                "the real values"
            )
        new_auth = dict(auth_state)
        if encryption_applies(
            getattr(c, "kind", None), getattr(c, "subclass_slug", "")
        ):
            new_auth = encrypt_auth_state(new_auth)
        c.auth_state = new_auth
    if mapping_profile is not None:
        c.mapping_profile = mapping_profile
    if permissions is not None:
        c.permissions = list(permissions)
    if capabilities is not None:
        c.capabilities = list(capabilities)
    c.updated_at = utc_now_iso()
    await c.save()
    return c


async def delete_connector(connector_id: str) -> bool:
    """Hard-delete a Connector. Returns ``True`` when deleted, ``False`` when
    the connector is not found. Phase 8 Plan 08-02 Task 1 helper.
    """
    c = await Connector.get(connector_id)
    if c is None:
        return False
    await c.delete()
    return True


async def list_connectors_for_owner(owner_id: str) -> List[Connector]:
    """List Connectors owned by ``owner_id`` via the Owns edge.

    Phase 1 implementation: prefer edge traversal (verified idiom from
    ``backend/app/agentive/services/channel_identity.py:135-137``); on any
    traversal failure or empty result, fall back to scalar-field
    ``Connector.find(owner=...)`` (verified idiom from
    ``channel_identity.py:64`` — note the project's jvspatial uses
    ``Node.find``, NOT ``find_many``).

    The dual-path lookup keeps the contract intact while Phase 2 (audit log)
    and Phase 5 (sync semantics) deepen the edge usage.
    """
    # Edge traversal first.
    try:
        from app.models.nodes import User  # verified path

        user = await User.get(owner_id)
        if user is not None:
            connectors = await user.nodes(
                edge=["Owns"],
                direction="out",
                node=["Connector"],
            )
            if connectors:
                return list(connectors)
    except Exception as e:
        logger.warning(
            "connector_registry_node: edge-traversal lookup failed for %s: %s; "
            "falling back to scalar filter",
            owner_id,
            e,
        )

    # Scalar fallback — verified Node.find API.
    try:
        results = await Connector.find(owner=owner_id)
        return list(results)
    except Exception as e:
        logger.warning(
            "connector_registry_node: scalar fallback lookup failed for %s: %s",
            owner_id,
            e,
        )
        return []
