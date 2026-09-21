"""Edge definitions for Integral relationships.

Structural links (Root→App, App→registries, OperationalModel→Views) use
the base ``Edge`` class via ``connect()`` with ``edge=None``.
"""

from typing import Optional

from jvspatial.core import Edge


class CATALOGS(Edge):
    """Registry or catalog membership.

    From: Users|Apps|Workspaces|Tracks|Invitations|Views|Dashboards|OperationalModel|OperationalModels
    → respective instances
    """

    cataloged_at: Optional[str] = None
    bidirectional: bool = False


class OWNS(Edge):
    """Ownership: User → Track | Workspace | App."""

    role: str = "owner"
    granted_at: Optional[str] = None
    bidirectional: bool = False


class IS_MEMBER_OF(Edge):
    """Workspace membership: User → Workspace.

    Role is one of: ``owner | admin | member | guest``. Defaults derive
    permissions; ``can_create_apps`` / ``can_create_tracks`` flags act as
    per-member overrides on top of role. Guest members are NOT included in
    the ``visibility="workspace"`` cascade — they only see Apps/Tracks
    where they are an explicit ``COLLABORATES_ON`` target.

    Personal-kind workspaces traditionally hold only their implicit owner;
    cross-workspace sharing now grants ``role="guest"`` edges on Personal
    workspaces too, so the resource surfaces in the recipient's /me/shared
    feed and WorkspaceSwitcher. ``admin`` / ``member`` roles remain
    organization-only (no use case on a single-user Personal).
    """

    role: str = "member"
    joined_at: Optional[str] = None
    can_create_apps: bool = False
    can_create_tracks: bool = False
    bidirectional: bool = False


class COLLABORATES_ON(Edge):
    """Collaboration on a App, Track, or Entry.

    Role hierarchy: ``owner | admin | editor | commenter | viewer``.

    * ``owner``     — transfer-of-ownership grant; primary ownership still
                      lives on the ``OWNS`` edge. Inherited "owner" caps to
                      "editor" on the child (see ``permissions._cap_inherited_role``;
                      track-config authority never cascades — I-ROLE-02).
    * ``admin``     — curator tier; full track-config authority (schema,
                      views, tags, library, anchors, migrations) plus entry
                      CRUD. NOT permitted to manage collaborators / mint
                      share links / delete the resource (owner-only).
    * ``editor``    — entry CRUD only; cannot mutate track-config substrate.
    * ``commenter`` — read + comment + react; may edit own entries via the
                      author-shortcut in ``can_edit_entry``.
    * ``viewer``    — read-only.

    App-level collaborators cascade to contained Tracks (and Entries)
    unless the child opts out of workspace-wide visibility inheritance via
    ``visibility="private"`` or a per-user ``EXCLUDED_FROM`` edge blocks the
    inherited path. Direct ``OWNS`` / ``COLLABORATES_ON`` on the parent App
    still cascade to private Tracks. ``ROLE_RANK`` ordering:
    ``owner(5) > admin(4) > editor(3) > commenter(2) > viewer(1)``.
    """

    role: str = "editor"
    invited_at: Optional[str] = None
    invited_by: Optional[str] = None
    bidirectional: bool = False


class HasAgentPreference(Edge):
    """User → Workspace per-user × per-workspace active agent.

    Records the agent the user has actively selected within a given
    workspace. Idempotent — at most one edge per (user, workspace) pair,
    enforced by ``services/edge_upsert.ensure_edge``. Latest write wins;
    ``updated_at`` is refreshed on each set.

    The active selection is read into a new ``ChatThread``'s ``agent_id``
    at create time so each thread is bound to one agent for its lifetime
    (the resolution chain in Task 5 of the agent-switcher plan).

    Phase 1 D-07 convention: PascalCase class + ALL_CAPS module-level
    alias. Downstream code should import the ALL_CAPS alias
    ``HAS_AGENT_PREFERENCE``; the class itself is exported for
    type-introspection.
    """

    provider_id: str = ""
    agent_id: str = ""
    updated_at: Optional[str] = None  # ISO-8601
    bidirectional: bool = False


# Phase 1 D-07 ALL_CAPS alias for downstream imports
# (e.g. ``from app.models.edges import HAS_AGENT_PREFERENCE``).
HAS_AGENT_PREFERENCE = HasAgentPreference


class EXCLUDED_FROM(Edge):
    """Explicit access denial: User → App | Track | Entry.

    Integral's access model is *inheritance with explicit deny*. A user who
    has access to a resource via parent cascade can be explicitly removed
    from that specific resource by creating this edge. The permission
    resolver returns ``None`` when this edge exists, blocking inheritance
    only. Direct ``OWNS`` and ``COLLABORATES_ON`` edges still beat
    exclusion — owners and explicitly added collaborators are not denied
    by an exclusion edge (which is normally created against inherited
    users to selectively prune cascade reach).
    """

    excluded_at: Optional[str] = None
    excluded_by: Optional[str] = None
    reason: Optional[str] = None
    bidirectional: bool = False


class CONTAINS(Edge):
    """Containment.

    Sources/targets:
        Workspace    → App, Track   (standalone tracks only; no Workspace→Track
                                       when the track is contained by a App —
                                       single-parent rule)
        App        → Track
        App        → Skill        (Phase 10 / Plan 10-04 — App-bundled skills
                                       registered at install time via
                                       ``agentive.services.skill_registry``)
        Track        → Entry
        ChatThread   → ChatMessage
        OperationalModel → EntryType, Tag
    """

    added_at: Optional[str] = None
    position: Optional[int] = None
    bidirectional: bool = False


class IS_OF_TYPE(Edge):
    """Entry → EntryType."""

    assigned_at: Optional[str] = None
    bidirectional: bool = False


class TAGGED_WITH(Edge):
    """Entry → Tag."""

    tagged_at: Optional[str] = None
    tagged_by: Optional[str] = None
    bidirectional: bool = False


class REFERENCES(Edge):
    """Entry → Entry relation materialized from relation fields.

    Phase 10 Plan 10-06 (APP-CROSS-RELATIONS-01) — added ``target_app_id``
    as an associative typed field (jvspatial pillar 2: relationship state on
    the edge). When the REFERENCES edge crosses App boundaries (the source
    Entry lives in App A; the target Entry lives in App B), ``target_app_id``
    carries the target App's id. For intra-App references it stays ``None``.

    The single safe reader is
    ``app.services.relation_runtime.read_cross_app_label`` — direct reads of
    ``label_field`` content elsewhere are forbidden (Risk 4 / Pitfall 5 — see
    grep-gate test in ``tests/test_cross_app_relations.py``).
    """

    field_key: Optional[str] = None
    relation_type: str = "operational_model"
    cross_track: bool = False
    # Phase 10 Plan 10-06 (APP-CROSS-RELATIONS-01) — associative typed field
    # for cross-App relation provenance. Set server-side at materialization;
    # never client-supplied. Validated against the target Entry's App at
    # write time (T-10-06-10 mitigation).
    target_app_id: Optional[str] = None
    bidirectional: bool = False


class HAS_COMMENT(Edge):
    """Entry | Comment → Comment."""

    created_at: Optional[str] = None
    bidirectional: bool = False


class AUTHORED_BY(Edge):
    """Entry | Comment → User."""

    authored_at: Optional[str] = None
    bidirectional: bool = False


class MENTIONS(Edge):
    """Comment → User."""

    mentioned_at: Optional[str] = None
    bidirectional: bool = False


class HAS_ATTACHMENT(Edge):
    """Entry → Attachment, User → Attachment (avatar variants), also
    ChatThread → Attachment (chat-uploaded files, Slice B).

    Phase 9 Plan 09-01 (AVT-01) — ``role`` + ``size`` are associative-edge
    fields so a single User can carry multiple avatar variants and the
    GET /avatar endpoint queries by ``role == "avatar" AND size == <n>``.
    Existing Entry → Attachment edges remain valid (``role == None``
    denotes a legacy attachment). Per jvspatial pillar 2: relationship
    state lives on the edge, not the node.

    Chat-owned attachments (``Attachment.owner_kind == "chat"``) wire this
    edge from the owning ``ChatThread`` instead of an ``Entry`` — this
    satisfies I-GRAPH-01 (ChatThread is rooted via ``User —OWNS→
    ChatThread``) without a new edge class.
    """

    attached_at: Optional[str] = None
    attached_by: Optional[str] = None
    bidirectional: bool = False
    # Phase 9 Plan 09-01 (AVT-01) — associative-edge state.
    role: Optional[str] = None  # "avatar" | None (legacy attachment)
    size: Optional[int] = None  # avatar variant pixel size (32/64/128/256)


class USES_TEMPLATE(Edge):
    """Track → Track (template)."""

    bidirectional: bool = False


class HAS_OPERATIONAL_MODEL(Edge):
    """App|Track → attached OperationalModel."""

    attached_at: Optional[str] = None
    bidirectional: bool = False


class HasApplicationDefinition(Edge):
    """App → ApplicationDefinition immutable contract revision."""

    revision: int = 0
    activated_at: Optional[str] = None
    bidirectional: bool = False


HAS_APPLICATION_DEFINITION = HasApplicationDefinition


class DEFINES_TRACK_PROFILE(Edge):
    """App-attached OperationalModel → track-template OperationalModel."""

    defined_at: Optional[str] = None
    bidirectional: bool = False


class INVITED_TO(Edge):
    """Invitation → Workspace (organization-kind destination of an invite)."""

    issued_at: Optional[str] = None
    bidirectional: bool = False


class HAS_NOTIFICATION(Edge):
    """User → Notification."""

    created_at: Optional[str] = None
    bidirectional: bool = False


# Note: ChangeEvent rows live as DBLog rows in the logging database
# (see app/services/change_event_logger.py) with log_level="CHANGE_EVENT".
# They are not Nodes in the prime graph, so they cannot participate in graph
# edges. Actor and resource identity are denormalized inline on every row
# (actor_id, resource_type, resource_id, scope).


class HasPolicy(Edge):
    """Subject (User | AgentConfig | Connector) → Policy.

    Per CONTEXT D-04: authorization is graph-local. The HAS_POLICY edge is
    walked by ``policy_engine.evaluate`` to find policies attached to a
    subject; absence of any HAS_POLICY edge on an AgentConfig (or Connector)
    means "fail-closed, no access" (POL-03 default-agent rule).

    Mirrors the Phase 1 D-07 PascalCase + ALL_CAPS pattern from
    ``app/agentive/edges.py:61-87`` — downstream code should import the
    ALL_CAPS alias ``HAS_POLICY``; the class itself is exported for
    type-introspection.
    """

    bidirectional: bool = False


# Phase 1 D-07 ALL_CAPS alias for downstream imports
# (e.g. ``from app.models.edges import HAS_POLICY``).
HAS_POLICY = HasPolicy


class TemplatedFrom(Edge):
    """Track → OperationalModel template-of-origin provenance (Phase 3.1 ANC-04).

    Written when ``materialize_anchor_track`` (Plan 03.1-02) auto-provisions a
    Track from a ``app.track_templates[]`` entry. The anchored Track receives
    its template's OperationalModel by reference via ``HAS_OPERATIONAL_MODEL`` (same
    polymorphic-target pattern many Tracks may share one CP), and ``TEMPLATED_FROM``
    is the ADDITIONAL lineage pointer that records *which template* produced
    this Track.

    Distinct from:
      - ``USES_TEMPLATE`` (Track → Track) — left for legacy seed-template
        provenance and NOT overloaded by Phase 3.1.
      - ``HAS_OPERATIONAL_MODEL`` (App|Track → OperationalModel) — the attached
        CP that drives the Track's runtime entry-type / view / taxonomy
        surface. ``TEMPLATED_FROM`` points at the SAME CP for templated
        anchored Tracks, but the edge semantics are "lineage / provenance"
        rather than "current attached operational model".

    Phase 1 D-07 convention: PascalCase class + ALL_CAPS module-level alias.
    Downstream code should import the ALL_CAPS alias ``TEMPLATED_FROM``; the
    class itself is exported for type-introspection.
    """

    template_key: Optional[str] = None
    provisioned_at: Optional[str] = None
    bidirectional: bool = False


# Phase 1 D-07 ALL_CAPS alias for downstream imports
# (e.g. ``from app.models.edges import TEMPLATED_FROM``).
TEMPLATED_FROM = TemplatedFrom


class Anchors(Edge):
    """Entry → Track anchor association (Phase 3.1 ANC-01).

    Materialized when an ``EntryType.fields[].type == "relation"`` field declares
    ``relation.target == "track"``. The anchored Track lives as a normal Track
    under its App (via CONTAINS); the ANCHORS edge is an ADDITIONAL pointer,
    NOT a containment edge.

    Cascade semantics are convention-level, driven by OperationalModel governance
    policy evaluated through ``policy_engine.evaluate`` (Plan 03.1-03) — NOT
    hard-coded substrate behavior.

    Phase 1 D-07 convention: PascalCase class + ALL_CAPS module-level alias.
    Downstream code should import the ALL_CAPS alias ``ANCHORS``; the class
    itself is exported for type-introspection.
    """

    field_key: Optional[str] = None
    role: str = "detail"
    bidirectional: bool = False


# Phase 1 D-07 ALL_CAPS alias for downstream imports
# (e.g. ``from app.models.edges import ANCHORS``).
ANCHORS = Anchors


class HasMemberRef(Edge):
    """Entry → User member-reference (member field type).

    Materialized when an ``EntryType.fields[].type == "member"`` field is
    written. The ``User`` is already rooted via the ``Users`` registry
    (``CATALOGS`` edge); ``HAS_MEMBER_REF`` is an ADDITIONAL pointer from
    the ``Entry``, the same shape category as ``ANCHORS`` — never a
    containment edge.

    ``field_key`` is a first-class associative typed field (which
    EntryType field produced the link), mirroring ``REFERENCES.field_key``
    at line 183. NEVER stuff this in an ``edge.context`` dict — jvspatial
    pillar 2 (semantics on edges) is non-negotiable here.

    Single-writer invariant: ``HAS_MEMBER_REF`` writes occur only inside
    ``_sync_member_ref_edges`` in
    ``backend/app/services/operational_model_graph.py``, mirroring the
    ANCHORS discipline at INVARIANTS.md L101-116. Direct
    ``entry.connect(user, edge=HAS_MEMBER_REF, ...)`` calls in API
    handlers, seed files, and agent tools are forbidden. The grep gate
    is enforced in CI:

        grep -rE "edge=HAS_MEMBER_REF|edge=HasMemberRef\b" backend/app/ \
            --include="*.py" \
            | grep -v __pycache__ \
            | grep -v _sync_member_ref_edges \
            | grep -v test_ \
            | wc -l   # expect 0

    Phase 1 D-07 convention: PascalCase class + ALL_CAPS module-level alias.
    Downstream code should import the ALL_CAPS alias ``HAS_MEMBER_REF``.
    """

    field_key: Optional[str] = None
    bidirectional: bool = False


# Phase 1 D-07 ALL_CAPS alias for downstream imports
# (e.g. ``from app.models.edges import HAS_MEMBER_REF``).
HAS_MEMBER_REF = HasMemberRef


class HasDraftProfile(Edge):
    """Published OperationalModel → draft OperationalModel (Phase 10.5 Plan 10.5-10 — I-GRAPH-01 wire).

    Materialized at ``fork_draft`` time. The draft variant hangs off
    its published parent so the in-flight authoring state is reachable
    from the rooted OperationalModel subgraph.

    Lifecycle:

    - ``publish_draft`` keeps the draft node (flips its status to
      ``"published"`` and stamps ``published_id`` for audit). The edge
      remains as a permanent provenance pointer.
    - ``discard_draft`` deletes the draft node. The edge cascades.

    Phase 1 D-07 convention: PascalCase class + ALL_CAPS module alias.
    """

    forked_at: Optional[str] = None
    bidirectional: bool = False


HAS_DRAFT_PROFILE = HasDraftProfile


class HasGovernancePolicy(Edge):
    """OperationalModel → Policy (Phase 10.5 Plan 10.5-09b — I-GRAPH-01 wire).

    Governance Policies with ``subject_kind="system"`` (materialized by
    ``policy_registry.materialize_governance_policies_for_operational_model``
    on OperationalModel publish) have no concrete subject Node to wire
    ``HAS_POLICY`` against — their subject_id is the string
    ``f"governance:{cp_id}"``. This edge attaches the governance
    Policy to the OperationalModel it governs so the row is reachable
    from the rooted CP subgraph.

    Phase 1 D-07 convention: PascalCase class + ALL_CAPS module alias.
    """

    materialized_at: Optional[str] = None
    bidirectional: bool = False


HAS_GOVERNANCE_POLICY = HasGovernancePolicy


class HasApproval(Edge):
    """Policy → Approval (Phase 10.5 Plan 10.5-05 — I-GRAPH-01 wire).

    Wired at ``policy_engine.evaluate`` time when a matched Policy with
    ``requires_human_approval=True`` produces an Approval row. The
    Policy is the deferred output's natural parent — the Approval
    exists because the Policy matched.

    Phase 1 D-07 convention: PascalCase class + ALL_CAPS module alias.
    """

    created_at: Optional[str] = None
    bidirectional: bool = False


HAS_APPROVAL = HasApproval


class HasApprovalDecision(Edge):
    """User → Approval (Phase 10.5 Plan 10.5-05 — I-GRAPH-01 wire).

    Wired at ``approve_approval`` / ``reject_approval`` time when a
    decider resolves a pending Approval. Cross-link to the User who
    decided; the primary HAS_APPROVAL edge from the originating Policy
    is what makes the Approval reachable from Root.

    Phase 1 D-07 convention: PascalCase class + ALL_CAPS module alias.
    """

    decided_at: Optional[str] = None
    decision: Optional[str] = None  # "approved" | "rejected"
    bidirectional: bool = False


HAS_APPROVAL_DECISION = HasApprovalDecision


class HasConflict(Edge):
    """Entry → Conflict (Phase 10.5 Plan 10.5-04 — I-GRAPH-01 wire).

    Materialized at ``create_conflict_record`` time. Wires the
    sync-runtime-detected divergence into the rooted Entry subgraph so
    the audit walker, cascade delete, and future Conflict-resolution
    walkers can reach it.

    Touches I-SYNC-04 (conflict records persist as Nodes; resolve
    inherits entry.update permission) — the invariant's semantics are
    unchanged, the wire adds REACHABILITY only.

    Phase 1 D-07 convention: PascalCase class + ALL_CAPS module alias.
    """

    detected_at: Optional[str] = None
    bidirectional: bool = False


HAS_CONFLICT = HasConflict


class HasUploadSession(Edge):
    """Entry → UploadSession (Phase 10.5 Plan 10.5-03 — I-GRAPH-01 wire).

    Materialized at ``start_upload_session`` time. The session is a
    transient child of its parent Entry — on finalize the session row
    is deleted (cascade removes the edge); on expiry / cancel the
    sweep job deletes the session and edge together.

    Phase 1 D-07 convention: PascalCase class + ALL_CAPS module alias.
    """

    started_at: Optional[str] = None
    bidirectional: bool = False


HAS_UPLOAD_SESSION = HasUploadSession


class HasShareLink(Edge):
    """Resource → ShareLink (Phase 10.5 Plan 10.5-02 — I-GRAPH-01 wire).

    Source/target:
        App | Track | Entry → ShareLink

    Materialized once at ``mint_share_link`` time. The ShareLink is
    deletable independently (revoke flips ``revoked_at`` but keeps the
    edge for audit); deleting the resource cascades through the edge
    via jvspatial's standard cascade-delete contract.

    Phase 1 D-07 convention: PascalCase class + ALL_CAPS module alias.
    """

    attached_at: Optional[str] = None
    bidirectional: bool = False


HAS_SHARE_LINK = HasShareLink


class HasConnector(Edge):
    """Workspace → Connector (ADR-009 §4 — I-GRAPH-01 wire).

    A mounted connector belongs to a workspace: its tools register into that
    workspace's tool surface, and its lifecycle routes gate on workspace
    membership. Until this edge existed the only structural attachment was
    ``User —OWNS→ Connector`` plus a denormalized ``Connector.workspace_id``
    scalar, so a connector was invisible to any walk that starts at the
    Workspace — including cascade-delete when the workspace goes away. The
    scalar stays as the fast-path cache; this edge is the relationship.

    Phase 1 D-07 convention: PascalCase class + ALL_CAPS module alias.
    """

    mounted_at: Optional[str] = None
    bidirectional: bool = False


HAS_CONNECTOR = HasConnector


class IsConnectedTo(Edge):
    """Phase 5 Plan 05-01 — Connector → Track binding.

    Locked decision #9 — replaces overloading ``Connector.mapping_profile``.
    One connector can bind to multiple Tracks (e.g. one GitHub Issues
    connector pulling issues into Track A and PRs into Track B). The
    per-binding mapping spec is carried on the edge.

    The mapping YAML is validated at sync time by the connector subclass'
    ``to_entry`` projection — this edge does NOT validate the YAML shape.

    Phase 1 D-07 convention: PascalCase class + ALL_CAPS module-level alias.
    Downstream code should import the ALL_CAPS alias ``IS_CONNECTED_TO``; the
    class itself is exported for type-introspection.
    """

    mapping_profile_yaml: str = ""
    bidirectional: bool = False


# Phase 1 D-07 ALL_CAPS alias for downstream imports
# (e.g. ``from app.models.edges import IS_CONNECTED_TO``).
IS_CONNECTED_TO = IsConnectedTo


class WATCHES(Edge):
    """User → Entry: user is watching this entry for updates."""

    watched_at: Optional[str] = None
    bidirectional: bool = False
