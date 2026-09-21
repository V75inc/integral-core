"""Policy + PolicyAction Pydantic schemas — wire shape for the authorization engine.

POL-01 + POL-02 + POL-04: schema mirrors Phase 1's typed-boundary convention and
Phase 2's audit.py module structure. The jvspatial Node `Policy` (in app/models/nodes.py,
landed by Plan 03-03) is the persistence shape; the types here are the API/engine shape.

Per CONTEXT D-10 (Phase 2 invariant — preserved in Phase 3): Subject.kind reuses
ActorKind from app.schemas.provenance — single source of truth across
Provenance.source, Actor.kind (audit log), and Subject.kind (policy engine). DO NOT
redefine the four-member Literal anywhere else in the repo. The grep gate
``grep -rE "^ActorKind\\s*=\\s*Literal" backend/app/ --include='*.py' | grep -v __pycache__``
MUST continue to return exactly 1 match.

Per CONTEXT D-12: PolicyAction is a STRICT-SUPERSET of ChangeEventAction. Every
audited mutation has a corresponding policy action (so ``policy_engine.evaluate``
accepts the same strings ``emit_change_event`` accepts), plus read variants and
policy.* CRUD additions (incl. policy.deny). The grep gate
``grep -rE "^PolicyAction\\s*=\\s*Literal" backend/app/ --include='*.py' | grep -v __pycache__``
MUST return exactly 1 match.

When the Phase 2 ChangeEventAction Literal grows (it has been a strict-superset
expansion idiom since Plan 02-02 Sub-task 1a; see audit.py docstring), this Literal
MUST be expanded in lockstep — the Section 1 test
``test_policy_action_strict_supersets_change_event_action`` is the CI lock.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel

from app.schemas.provenance import (  # D-10 single source of truth — DO NOT redefine
    ActorKind,
)

PolicyAction = Literal[
    # ---- Mutation actions — strict-superset of ChangeEventAction (Phase 2 D-09) ----
    # Core resource mutations
    "entry.create",
    "entry.update",
    "entry.delete",
    "track.create",
    "track.update",
    "track.delete",
    "space.create",
    "space.update",
    "space.delete",
    "operational_model.create",
    "operational_model.update",
    "operational_model.delete",
    "operational_model.merge_library",
    "operational_model.publish",
    "tag.create",
    "tag.update",
    "tag.delete",
    "view.create",
    "view.update",
    "view.delete",
    "dashboard.create",
    "dashboard.update",
    "dashboard.delete",
    "comment.create",
    "comment.update",
    "comment.delete",
    # Deleting SOMEONE ELSE's comment — authorization-only, with no matching
    # ChangeEventAction: the resulting mutation is still audited as
    # ``comment.delete`` (flagged ``_moderated`` in the before-snapshot), so
    # this stays out of ChangeEventAction and the strict-superset lock holds.
    # Admin-tier in policy_engine, NOT the commenter tier its namesakes above
    # ride — see the ``_MODERATION_ACTIONS`` note there.
    "comment.moderate",
    "attachment.create",
    "attachment.delete",
    "entry_type.create",
    "entry_type.update",
    "entry_type.delete",
    "notification.create",
    "notification.delete",
    # Phase 9 Plan 09-02 (NOTIF-01) — mark-all-read state transition.
    # notification.read already listed below in the read-action block — same
    # string, dual semantic role (read-gate + state-transition). The
    # ChangeEventAction strict-superset gate
    # (test_policy_action_strict_supersets_change_event_action) keeps these
    # in lockstep with the audit.py additions.
    "notification.mark_all_read",
    # Prompt Sheet queue mutations — mirror of the audit.py additions, kept in
    # lockstep by test_policy_action_strict_supersets_change_event_action.
    "prompt_queue.resolve_question",
    "prompt_queue.mark_write",
    "prompt_queue.cancel_all",
    # Agentive mutations
    "agent_config.register",
    "agent_config.update",
    "agent_config.heartbeat",
    "connector.create",
    "connector.update",
    "connector.delete",
    "channel_identity.create",
    "channel_identity.update",
    "channel_identity.delete",
    "channel_identity.verify",
    # User mutations
    "user.create",
    "user.update",
    "user.delete",
    # Workspace primitive — Personal + Organization unified.
    "workspace.create",
    "workspace.update",
    "workspace.delete",
    "workspace.member_add",
    "workspace.member_update",
    "workspace.member_remove",
    "workspace.invitation_create",
    "workspace.invitation_accept",
    "workspace.invitation_decline",
    "workspace.invitation_revoke",
    "workspace.invitation_expire",
    # Sharing primitives (resource = space | track | entry)
    "space.collaborator_add",
    "space.collaborator_remove",
    "space.collaborator_role_update",
    "space.exclusion_add",
    "space.exclusion_remove",
    "space.share_link.mint",
    "space.share_link.revoke",
    "space.share_link.redeem",
    "space.invitation_create",
    "track.collaborator_add",
    "track.collaborator_remove",
    "track.collaborator_role_update",
    "track.exclusion_add",
    "track.exclusion_remove",
    "track.share_link.mint",
    "track.share_link.revoke",
    "track.share_link.redeem",
    "track.invitation_create",
    "entry.collaborator_add",
    "entry.collaborator_remove",
    "entry.collaborator_role_update",
    "entry.exclusion_add",
    "entry.exclusion_remove",
    "entry.share_link.mint",
    "entry.share_link.revoke",
    "entry.share_link.redeem",
    "entry.invitation_create",
    # ---- Phase 3 additions: policy.* CRUD + denial action ----
    "policy.create",
    "policy.update",
    "policy.delete",
    "policy.deny",
    # ---- Phase 3 read actions (D-12 — locked enumeration) ----
    "entry.read",
    "track.read",
    "space.read",
    "operational_model.read",
    "tag.read",
    "view.read",
    "comment.read",
    "attachment.read",
    "agent_config.read",
    "connector.read",
    "policy.read",
    "audit_log.read",
    "event_feed.subscribe",
    "notification.read",
    "organization.read",
    "user.read",
    "routine_task.read",
    # ---- Phase 3.1 Plan 03.1-03 additions (additive — strict-superset; mirrors
    # Phase 3 03-05 idiom). Governance Policy actions for anchor associations
    # (ANC-03 + ANC-05). The denial action ``anchor.deny`` mirrors the
    # ``policy.deny`` precedent and is listed here as well as in
    # ChangeEventAction so the strict-superset invariant
    # (test_policy_action_strict_supersets_change_event_action) continues to
    # hold. PolicyAction listing ``anchor.deny`` is harmless — the denial-emit
    # path fires via ``policy.deny`` and ``anchor.deny`` ChangeEvents only;
    # callers never evaluate(action='anchor.deny').
    "anchor.create",
    "anchor.delete",
    "anchor.cascade",
    "anchor.deny",
    # ---- Phase 5 Plan 05-01 additions (CONTEXT locked decision #5) ----
    # New connector.sync.* + migration.* + conflict.resolve actions.
    # Single-Literal AST grep gate UNCHANGED — these are members of the
    # one existing definition. PolicyAction strict-supersets the new
    # ChangeEventAction members (INVARIANTS.md L43-63 gate).
    "connector.sync",  # per-connector sync gate (per-attempt)
    "connector.sync.start",  # mirrors ChangeEventAction (D-12 strict-superset)
    "connector.sync.complete",  # mirrors ChangeEventAction
    "connector.sync.failed",  # mirrors ChangeEventAction
    "migration.publish",  # normal-publish gate (D-12 strict-superset)
    "migration.force_publish",  # destructive escape — separate from migration.publish
    "migration.run",  # async runner gate (mirrors ChangeEventAction)
    "conflict.resolve",  # POST /api/conflicts/{id}/resolve gate
    # ---- Profile-authoring gate action ----
    # ``operational_model.author`` is PolicyAction-only by design (gate without separate
    # audit event — same precedent as ``migration.publish`` / ``conflict.resolve``;
    # the corresponding audit signal is the existing ``operational_model.create``).
    # (The former A2A gate actions ``agent.discover`` / ``a2a.delegate`` were
    # retired with the agent-to-agent fabric — ADR-003.)
    "operational_model.author",  # gate on POST /api/operational-models/author
    # ---- Phase 7 Plan 07-04 additions ----
    # PolicyAction-only gate actions for the approval review surface.
    # Mirror the ``migration.publish`` / ``conflict.resolve`` precedent —
    # gate without separate audit twin; audit signal is the re-run's
    # ORIGINAL action (e.g. entry.create) for approve, or ``policy.deny``
    # for reject. Single-Literal AST grep gate UNCHANGED — these are members
    # of the one existing definition.
    "approval.approve",  # gate on POST /api/approvals/{id}/approve
    "approval.reject",  # gate on POST /api/approvals/{id}/reject
    # ---- Phase 10 Plan 10-05 additions ----
    # App lifecycle actions (I-CHA strict-superset gate). The three audit-twin
    # members mirror the ChangeEventAction additions in lockstep. The two
    # additional PolicyAction-only members (``app.install`` / ``app.uninstall``)
    # gate the action verbs at the API edge — they have no separate audit
    # twin (precedent: ``migration.publish`` / ``conflict.resolve`` /
    # ``operational_model.author``). The audit
    # signal for a successful install is ``app.installed``; for a successful
    # uninstall it is ``app.uninstalled`` (or ``app.force_uninstalled`` for
    # the force path).
    "app.installed",
    "app.uninstalled",
    "app.force_uninstalled",
    "app.install",
    "app.uninstall",
    # App primitive CRUD — mirrors ChangeEventAction additions in lockstep
    # so the strict-superset invariant continues to hold.
    "app.create",
    "app.update",
    "app.delete",
    "app.read",
    # Sharing primitives — app resource (mirrors space.* above; new emissions use app.*).
    "app.collaborator_add",
    "app.collaborator_remove",
    "app.collaborator_role_update",
    "app.exclusion_add",
    "app.exclusion_remove",
    "app.share_link.mint",
    "app.share_link.revoke",
    "app.share_link.redeem",
    "app.invitation_create",
    "entry.archived",
    "pending_write.expired",
    # Mirrors ChangeEventAction "operational_model.rescan" — admin-driven
    # library bundle rescan. Strict-superset invariant preserved.
    "operational_model.rescan",
    # Mirrors ChangeEventAction reorder additions — workspace.apps_reorder
    # + app.tracks_reorder. Strict-superset invariant preserved.
    "workspace.apps_reorder",
    "app.tracks_reorder",
    # Mirrors ChangeEventAction "tool.invoke" — hook framework tool dispatch
    # audit. Strict-superset invariant preserved.
    "tool.invoke",
    "model_credential.upsert",
    "model_credential.revoke",
    # Routine tasks (user-issued recurring chat instructions) — mirrors
    # ChangeEventAction additions in audit.py in lockstep (strict-superset
    # gate). ``routine_task.read`` is PolicyAction-only, listed in the
    # read-action bucket below.
    "routine_task.create",
    "routine_task.update",
    "routine_task.delete",
    "routine_task.run_completed",
    "routine_task.auto_paused",
    "routine_task.completed",
    # Workspace skills editor (skills-editor v1) — mirrors ChangeEventAction.
    "skill.customized",
    "skill.created",
    "skill.deleted",
    "skill.reset",
    "skill.toggled",
    "staging.rollback",
]


ResourceKind = Literal[
    "entry",
    "track",
    "app",
    "dashboard",
    "operational_model",
    "tag",
    "view",
    "comment",
    "attachment",
    "agent_config",
    "connector",
    "policy",
    "change_event",
    "notification",
    "organization",
    "user",
]


class Subject(BaseModel):
    """Authorization subject — who is acting (D-01).

    Subject.kind reuses ActorKind from app.schemas.provenance (D-10 invariant).
    Handlers MUST construct Subject from the authenticated principal
    (request.state.user / resolve_principal_id) — NEVER from client request body.
    The Plan 02 sweep audits every call site to enforce this; the
    ``extra: forbid`` model_config on request bodies in Plan 03 prevents a
    client-supplied ``subject`` field from being silently accepted.
    """

    kind: ActorKind  # SAME Literal as Provenance.source AND Actor.kind
    id: str  # User.id | AgentConfig.id | Connector.id | "system"

    model_config = {"extra": "forbid"}


class Resource(BaseModel):
    """Authorization resource — what is being acted upon (D-01).

    ``scope`` is the standard scope-string format used across the platform:
    ``"track:<id>" | "app:<id>" | "user:<id>" | "org:<id>" | "*"``. The
    default-human path in ``policy_engine._evaluate_human_default`` dispatches on
    this string when the resource kind itself does not map to a single
    can_view_* helper (e.g. audit_log.read, event_feed.subscribe).
    """

    kind: ResourceKind
    id: str
    scope: str  # "track:<id>" | "app:<id>" | "user:<id>" | "org:<id>" | "*"
    entry_type: Optional[str] = None  # for filter matching when kind=="entry"
    tags: List[str] = []  # for filter matching

    model_config = {"extra": "forbid"}


class Decision(BaseModel):
    """Engine output — allow/deny + structured reason (POL-04 observability).

    ``reason`` is a free-string identifier (not a Literal) so future plans (and
    future-Phase Policy rules) can introduce new reasons additively. Known v1
    values:

        default_human_policy            — human allowed via legacy helper
        default_human_policy_denied     — human denied via legacy helper
        fail_closed_no_policy           — agent/connector with no Policy edge
        system_subject                  — Subject(kind="system", ...) bypass
        system_subject_internal         — recursion-guard _internal_actor bypass
        policy_match:<policy_id>        — Plan 03 — explicit Policy granted
        filter_mismatch:<field>         — Plan 03 — Policy filter denied
        requires_human_approval         — Phase 7 Plan 07-04 — agent write
                                          intercepted; ``approval_id`` set

    ``policy_chain`` is the ordered list of Policy IDs the engine consulted
    (Plan 03 wires graph traversal; Plan 1 leaves it empty for human/system
    bypass and fail-closed denials).

    ``approval_id`` is set ONLY when ``reason='requires_human_approval'``
    (Phase 7 Plan 07-04). Identifies the persisted Approval node the agent
    caller may poll / a human approver may approve|reject. This field is
    additive; Phase 3/4/5/6 callers default it to None.
    """

    allowed: bool
    reason: str
    matched_policy_id: Optional[str] = None
    policy_chain: List[str] = []  # ordered policy IDs evaluated (for observability)
    approval_id: Optional[str] = (
        None  # Phase 7 Plan 07-04 — set on requires_human_approval
    )

    model_config = {"extra": "forbid"}


# ---- CRUD wire shapes for /api/policies (Plan 03-03) ------------------------


class PolicyCreate(BaseModel):
    """Body for POST /api/policies (Plan 03-03 Task 3).

    ``created_by`` is intentionally NOT on this body — it derives from the
    authenticated principal in ``resolve_principal_id(request)``.
    ``extra: forbid`` blocks any client-side attempt to inject a forged
    ``created_by`` (T-03-03-T01 mitigation).
    """

    subject_kind: ActorKind = "agent"
    subject_id: str
    scope: str = "*"
    actions: List[str] = []
    entry_types: List[str] = []
    tags: List[str] = []
    requires_human_approval: bool = False
    is_active: bool = True

    model_config = {"extra": "forbid"}


class PolicyUpdate(BaseModel):
    """Body for PATCH /api/policies/{id} (Plan 03-03 Task 3).

    All fields optional — partial update. ``subject_kind``, ``subject_id``, and
    ``created_by`` are intentionally absent: re-targeting a Policy at a
    different subject would silently invalidate the HAS_POLICY edge, so
    callers must delete + re-create instead.
    """

    scope: Optional[str] = None
    actions: Optional[List[str]] = None
    entry_types: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    requires_human_approval: Optional[bool] = None
    is_active: Optional[bool] = None

    model_config = {"extra": "forbid"}


class PolicyResponse(BaseModel):
    """Wire response shape for GET / POST / PATCH /api/policies.

    Exposes all 9 locked schema fields (D-04) plus ``id`` and the audit
    timestamps. ``subject_kind`` is typed as ``ActorKind`` even though it is
    stored on the Node as ``str`` — the Pydantic boundary enforces the Literal
    at the API edge.
    """

    id: str
    subject_kind: ActorKind
    subject_id: str
    scope: str
    actions: List[str]
    entry_types: List[str]
    tags: List[str]
    requires_human_approval: bool
    is_active: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    created_by: Optional[str] = None

    model_config = {"extra": "forbid"}


# ---- F1 Phase One — Admin Forensic Loop (action explain) --------------------


class ExplainActionRequest(BaseModel):
    """Body for POST /api/policies/explain — forensic read, not a mutation.

    Unlike normal authorize paths, ``subject_*`` here is the *explained*
    actor (admin asks "why would this subject be allowed/denied?"). The
    endpoint still authorizes the *caller* separately.
    """

    subject_kind: ActorKind = "human"
    subject_id: str
    action: str
    resource_kind: ResourceKind
    resource_id: str
    resource_scope: str
    entry_type: Optional[str] = None
    tags: List[str] = []
    operation_key: Optional[str] = None
    app_id: Optional[str] = None

    model_config = {"extra": "forbid"}


class ExplainActionResponse(BaseModel):
    """Explain payload for admin forensic UI."""

    allowed: bool
    reason: str
    matched_policy_id: Optional[str] = None
    policy_chain: List[str] = []
    approval_id: Optional[str] = None
    operation_key: Optional[str] = None
    access_snapshot: Optional[dict] = None

    model_config = {"extra": "forbid"}
