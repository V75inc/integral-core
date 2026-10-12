"""Policy CRUD service — Plan 03-03 Task 2.

Mirrors Phase 1 ``connector_registry_node.py`` shape: kw-only args,
``utc_now_iso`` for ``created_at`` / ``updated_at``, best-effort ``HAS_POLICY``
edge wire with ``try/except`` + WARNING log on failure (Phase 1 D-07
fail-loud convention). Per CONTEXT D-02: Policy is core, attached to
``User | AgentConfig | Connector`` via the ``HAS_POLICY`` edge.

The agent / connector subject branches import their target Nodes
conditionally — ``AgentConfig`` and ``Connector`` live under
``app/agentive/`` and are loaded by the always-on agentive layer.
Persistence of the Policy Node itself NEVER depends on the agentive
layer (D-02 invariant).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.models.edges import HAS_POLICY
from app.models.nodes import Policy
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


async def _resolve_subject_node(subject_kind: str, subject_id: str) -> Optional[Any]:
    """Resolve the subject Node to wire ``HAS_POLICY`` from.

    For ``"human"`` subjects: ``User``. For ``"agent"`` / ``"connector"``:
    imported lazily at the registry boundary (mirrors the lazy-import
    convention in ``connector_registry_node.py`` for graph reads spanning the
    agentive boundary).

    Returns ``None`` when:
      - the subject_kind is ``"system"`` (system subjects are evaluate-time
        bypasses; they don't carry attached Policies)
      - the required agentive node type cannot be imported for an agent /
        connector subject
      - the resolved Node id is missing in the graph
    """
    if subject_kind == "human":
        from app.services.permissions import get_user_node

        return await get_user_node(subject_id)
    if subject_kind == "agent":
        try:
            from app.agentive.nodes import AgentConfig

            return await AgentConfig.get(subject_id)
        except ImportError:
            return None
    if subject_kind == "connector":
        try:
            from app.agentive.nodes import Connector

            return await Connector.get(subject_id)
        except ImportError:
            return None
    return None


async def create_policy(
    *,
    subject_kind: str,
    subject_id: str,
    scope: str = "*",
    actions: Optional[List[str]] = None,
    entry_types: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    requires_human_approval: bool = False,
    is_active: bool = True,
    created_by: Optional[str] = None,
) -> Policy:
    """Create a Policy Node and attach via ``HAS_POLICY`` edge to the subject.

    Per CONTEXT D-04 schema lock. Best-effort edge wire — Policy persists even
    if the subject Node lookup fails (mirrors Phase 1 D-07 convention).
    When the edge wire fails, ``list_policies_for_subject`` falls back to a
    ``Policy.find`` scan by ``(subject_kind, subject_id)`` so the Policy
    remains discoverable.
    """
    from app.exceptions import AuthorizationUnavailableError

    subject_node = await _resolve_subject_node(subject_kind, subject_id)
    if subject_node is None:
        raise AuthorizationUnavailableError()
    now = utc_now_iso()
    policy = await Policy.create(
        subject_kind=subject_kind,
        subject_id=subject_id,
        scope=scope,
        actions=actions or [],
        entry_types=entry_types or [],
        tags=tags or [],
        requires_human_approval=requires_human_approval,
        is_active=is_active,
        created_at=now,
        updated_at=now,
        created_by=created_by,
    )

    # HAS_POLICY edge wire (D-04 — graph-local lookup target).
    # When the subject Node is missing, Policy still persists and remains
    # discoverable via Policy.find (documented D-04 / Phase 1 D-07).
    # When the subject resolves but the wire raises, roll back — otherwise
    # we leave an orphan Policy that walkers cannot reach (I-GRAPH-01).
    try:
        await subject_node.connect(policy, edge=HAS_POLICY)
    except Exception as e:  # noqa: BLE001 — fail-loud convention
        logger.warning(
            "create_policy: HAS_POLICY edge wire failed: %s; "
            "rolling back orphaned Policy %s",
            e,
            policy.id,
        )
        try:
            await policy.delete()
        except Exception:
            logger.exception(
                "create_policy: rollback delete failed for policy=%s",
                policy.id,
            )
        raise

    return policy


async def get_policy(policy_id: str) -> Optional[Policy]:
    """Fetch a Policy by id."""
    return await Policy.get(policy_id)


def current_policy_body(row: dict, *, subject_kind: str, subject_id: str) -> dict:
    """Validate all primary grant facts before hydration can invent defaults."""
    from app.exceptions import AuthorizationUnavailableError

    body = row.get("context")
    if (
        not isinstance(body, dict)
        or body.get("subject_kind") != subject_kind
        or body.get("subject_id") != subject_id
    ):
        raise AuthorizationUnavailableError()
    if (
        subject_kind not in {"human", "agent", "connector", "system"}
        or type(subject_id) is not str
        or not subject_id
        or len(subject_id) > 256
    ):
        raise AuthorizationUnavailableError()
    for key in ("is_active", "requires_human_approval"):
        if type(body.get(key)) is not bool:
            raise AuthorizationUnavailableError()
    revision = body.get("delegation_revision")
    if type(revision) is not int or revision < 1:
        raise AuthorizationUnavailableError()
    for key in ("scope", "created_by"):
        value = body.get(key)
        if (
            type(value) is not str
            or not value
            or len(value) > 2048
            or value.strip() != value
        ):
            raise AuthorizationUnavailableError()
    for key in ("actions", "entry_types", "tags"):
        values = body.get(key)
        if (
            not isinstance(values, list)
            or len(values) > 256
            or any(
                type(value) is not str
                or not value
                or len(value) > 512
                or value.strip() != value
                for value in values
            )
            or len(values) != len(set(values))
        ):
            raise AuthorizationUnavailableError()
    return body


async def list_policies_for_subject(subject_kind: str, subject_id: str) -> List[Policy]:
    """Read only complete primary Policies with a unique named subject attachment.

    Malformed or orphan grants never receive model defaults or scalar fallback.
    """
    from jvspatial.core.context import get_default_context

    from app.exceptions import AuthorizationUnavailableError
    from app.services.authority_facts import primary_node_record

    subject_node = await _resolve_subject_node(subject_kind, subject_id)
    if subject_node is None:
        return []
    context = get_default_context()
    rows = await context.database.find(
        "edge",
        {"source": subject_node.id, "entity": HAS_POLICY._entity_name()},
        limit=257,
    )
    if len(rows) > 256:
        raise AuthorizationUnavailableError()
    policies = []
    seen = set()
    for edge in rows:
        identity = edge.get("target")
        if not isinstance(identity, str) or not identity or identity in seen:
            raise AuthorizationUnavailableError()
        seen.add(identity)
        row = await primary_node_record(context.database, Policy, identity)
        if row is None:
            raise AuthorizationUnavailableError()
        try:
            current_policy_body(row, subject_kind=subject_kind, subject_id=subject_id)
        except AuthorizationUnavailableError:
            # A disabled legacy source cannot execute or acquire defaults.
            # Preserve its raw row for explicit owner cleanup outside this evaluator.
            if row["context"].get("is_active") is False:
                continue
            raise
        roots = await context.database.find(
            "edge", {"target": identity, "entity": HAS_POLICY._entity_name()}, limit=2
        )
        if len(roots) != 1 or roots[0].get("source") != subject_node.id:
            raise AuthorizationUnavailableError()
        policy = await context._deserialize_entity(Policy, row)
        if policy is None:
            raise AuthorizationUnavailableError()
        policies.append(policy)
    return policies


async def update_policy(
    policy_id: str,
    *,
    scope: Optional[str] = None,
    actions: Optional[List[str]] = None,
    entry_types: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    requires_human_approval: Optional[bool] = None,
    is_active: Optional[bool] = None,
) -> Optional[Policy]:
    """Partial-update a Policy. Returns updated Policy or ``None`` if not found.

    ``subject_kind`` / ``subject_id`` / ``created_by`` are intentionally not
    updatable — re-targeting a Policy would silently invalidate the
    ``HAS_POLICY`` edge. Callers must delete + re-create to retarget.
    """
    p = await Policy.get(policy_id)
    if p is None:
        return None
    if scope is not None:
        p.scope = scope
    if actions is not None:
        p.actions = actions
    if entry_types is not None:
        p.entry_types = entry_types
    if tags is not None:
        p.tags = tags
    if requires_human_approval is not None:
        p.requires_human_approval = requires_human_approval
    if is_active is not None:
        p.is_active = is_active
    p.updated_at = utc_now_iso()
    p.delegation_revision += 1
    await p.save()
    return p


async def materialize_governance_policies_for_operational_model(
    cp_id: str,
    compiled_manifest: Dict[str, Any],
) -> List[Policy]:
    """Materialize governance Policies for anchor fields in a published CP.

    Phase 3.1 Plan 03.1-03 (ANC-03). Walks the compiled manifest for
    ``entry_types[*].fields[*]`` that declare ``type='relation' AND
    target='track'`` and creates one Policy row per field with:

      - ``subject_kind="system"`` (preserves Phase 2 D-10 ActorKind invariant —
        governance Policies do NOT introduce a new ActorKind member; see
        CONTEXT decisions Governance subject)
      - ``subject_id=f"governance:{cp_id}"`` (canonical opaque id; not a real
        User/AgentConfig/Connector node — system subjects are evaluate-time
        bypasses, see policy_engine.evaluate)
      - ``scope=f"anchor_field:{cp_id}:{entry_type_key}:{field_key}"``
        (locked format per CONTEXT.md Phase 3.1 ANC-03)
      - ``actions=["anchor.create"]`` always; ``"anchor.cascade"`` appended
        when manifest ``relation.governance.cascade == "hard"`` (per CONTEXT
        cascade lifecycle decision — ``preserve`` denies cascade)

    Stale governance Policies for the same ``cp_id`` are deleted before fresh
    rows are written (republish replaces, never accumulates). Stale-detection
    matches on the ``anchor_field:<cp_id>:`` scope prefix + ``subject_kind="system"``
    — narrow enough that human/agent Policies persisted via create_policy are
    untouched.

    Both the manifest compiler (track-scope) and the app-scope variant use
    the same shape; this helper accepts either by walking ``entry_types`` from
    the manifest root OR from ``manifest["track"]["entry_types"]`` (track scope)
    OR from ``manifest["app"]["track_templates"][*]["entry_types"]`` (app
    scope — track templates).

    Returns the new Policy list. Caller is responsible for invoking this
    at publish time (see operational_model_atomic_swap.publish_draft wiring).
    """
    # 1) Clean up stale governance Policies for this CP. The Policy.find query
    #    matches by stored Node attributes; we cast a wide net (subject_kind=system)
    #    and filter in-Python by scope prefix to avoid relying on Mongo-style
    #    prefix operators that jvspatial may not implement.
    stale_candidates = await Policy.find({"subject_kind": "system"})
    scope_prefix = f"anchor_field:{cp_id}:"
    stale = [p for p in stale_candidates if (p.scope or "").startswith(scope_prefix)]
    for p in stale:
        try:
            await p.delete(cascade=False)
        except Exception as e:  # noqa: BLE001 — fail-loud convention
            logger.warning(
                "materialize_governance_policies_for_operational_model: "
                "failed to delete stale governance Policy %s: %s",
                p.id,
                e,
            )

    # 2) Walk all entry_types in the compiled manifest and create fresh
    #    governance Policies for anchor fields.
    new_policies: List[Policy] = []
    now = utc_now_iso()

    def _entry_types_from_manifest(m: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Yield entry_types[] regardless of whether the manifest is track-scope or app-scope."""
        results: List[Dict[str, Any]] = []
        # Plain track-scope or library-package shape.
        if isinstance(m.get("entry_types"), list):
            results.extend(m["entry_types"])
        # Compiled track-scope: manifest["track"]["entry_types"].
        track_block = m.get("track")
        if isinstance(track_block, dict) and isinstance(
            track_block.get("entry_types"), list
        ):
            results.extend(track_block["entry_types"])
        # Compiled app-scope: manifest["app"]["tracks"][*]["entry_types"]
        # AND manifest["app"]["track_templates"][*]["entry_types"].
        app_block = m.get("app")
        if isinstance(app_block, dict):
            for track in app_block.get("tracks", []) or []:
                if isinstance(track, dict) and isinstance(
                    track.get("entry_types"), list
                ):
                    results.extend(track["entry_types"])
            for tmpl in app_block.get("track_templates", []) or []:
                if isinstance(tmpl, dict) and isinstance(tmpl.get("entry_types"), list):
                    results.extend(tmpl["entry_types"])
        return results

    for et in _entry_types_from_manifest(compiled_manifest):
        if not isinstance(et, dict):
            continue
        et_key = str(et.get("key") or "")
        if not et_key:
            continue
        for field in et.get("fields") or []:
            if not isinstance(field, dict):
                continue
            if field.get("type") != "relation":
                continue
            relation = field.get("relation") or {}
            if not isinstance(relation, dict):
                continue
            if relation.get("target") != "track":
                continue
            field_key = str(field.get("key") or "")
            if not field_key:
                continue
            governance = relation.get("governance") or {}
            cascade = (
                str(governance.get("cascade") or "hard").strip().lower()
                if isinstance(governance, dict)
                else "hard"
            )
            actions = ["anchor.create"]
            if cascade == "hard":
                actions.append("anchor.cascade")

            policy = await Policy.create(
                subject_kind="system",
                subject_id=f"governance:{cp_id}",
                scope=f"anchor_field:{cp_id}:{et_key}:{field_key}",
                actions=actions,
                entry_types=[],
                tags=[],
                requires_human_approval=False,
                is_active=True,
                created_at=now,
                updated_at=now,
                created_by="system:operational_model_publish",
            )
            # Phase 10.5 Plan 10.5-09b (I-GRAPH-01): wire
            # OperationalModel -HAS_GOVERNANCE_POLICY-> Policy. The Policy
            # has subject_kind="system" — no concrete subject node for
            # HAS_POLICY — so the governed CP is the canonical parent.
            try:
                from app.models.edges import HAS_GOVERNANCE_POLICY
                from app.models.nodes import OperationalModel

                cp = await OperationalModel.get(cp_id)
                if cp is not None:
                    await cp.connect(
                        policy, edge=HAS_GOVERNANCE_POLICY, materialized_at=now
                    )
            except Exception as e:
                logger.warning(
                    "materialize_governance_policies: HAS_GOVERNANCE_POLICY "
                    "wire failed for cp=%s policy=%s: %s",
                    cp_id,
                    policy.id,
                    e,
                )
            new_policies.append(policy)

    return new_policies


async def delete_policy(policy_id: str) -> bool:
    """Delete a Policy. Returns ``True`` if deleted, ``False`` if not found.

    Uses jvspatial's ``Node.delete()`` (cascading by default — see
    ``app/services/entry_deletion.py`` for the canonical idiom). The
    ``HAS_POLICY`` edge from the subject Node is cleaned up as part of the
    cascade.
    """
    p = await Policy.get(policy_id)
    if p is None:
        return False
    await p.delete()
    return True
