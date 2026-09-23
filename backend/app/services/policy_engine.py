"""policy_engine.evaluate — single authorization entry point (POL-01).

Per CONTEXT D-01: keyword-only signature; returns a Decision Pydantic model.
Per CONTEXT D-03: default human policy is SYNTHESIZED at evaluate-time, NOT a
persisted wildcard Policy row. Humans get today's behaviour by delegating to the
existing can_view_*/can_edit_*/can_delete_* helpers. The 37-test CRUD regression
suite at backend/tests/test_crud_*.py is the parity gate — it MUST pass unchanged.

Per CONTEXT D-04 + D-11: agents and connectors get fail-closed-by-default. Plan 03
adds the HAS_POLICY edge traversal that finds attached Policies; until then,
agents and connectors return Decision(allowed=False, reason="fail_closed_no_policy").

Per CONTEXT D-10: the system subject (kind="system") bypasses the engine entirely
— this is the recursion guard for the future denial-emit path (Plan 05). The
private _internal_actor kwarg lets internal callers force the system bypass.
NEVER expose _internal_actor outside this module.

Per RESEARCH §"Pitfall 1": the default-human branch DELEGATES; it does NOT
reimplement precedence rules from app/services/permissions.py.

Wave 1 contract:
  Plans 02-05 import:
    from app.services.policy_engine import evaluate
    from app.schemas.policy import Subject, Resource, Decision, PolicyAction
  Wave 1 finalizes those import paths; downstream waves are forbidden from
  re-defining or re-locating them.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from app.exceptions import PayloadTooLargeError
from app.middleware.permissions_cache import permissions_cache_get
from app.schemas.policy import Decision, Resource, Subject
from app.services.change_event import emit_change_event
from app.services.permissions import (
    ROLE_RANK,
    can_admin_app,
    can_admin_track,
    can_delete_app,
    can_delete_track,
    can_edit_app,
    can_edit_entry,
    can_edit_track,
    can_edit_view,
    can_view_app,
    can_view_entry,
    can_view_track,
    can_view_view,
    get_user_node,
    resolve_role,
)
from app.utils.time import utc_now, utc_now_iso

if TYPE_CHECKING:
    # Type-checking only — runtime import lives inside _find_policies_for_subject
    # so a circular dependency between policy_engine and policy_registry
    # (registry imports models, models stay independent of services) cannot
    # arise via import-order surprises.
    from app.models.nodes import Policy

logger = logging.getLogger(__name__)


def _action_matches(policy_actions: List[str], requested_action: str) -> bool:
    """Wildcard-aware action matching (CONTEXT.md <specifics>).

    ``"*"`` matches any action. ``"<resource>.*"`` matches any
    ``"<resource>.<verb>"`` action. Exact string match otherwise. Used by Plan
    03's Policy Node lookup; v1 default-human path does not call this
    (delegates to legacy helpers).

    Examples::

        _action_matches(["*"], "entry.create")         # True
        _action_matches(["entry.*"], "entry.create")   # True
        _action_matches(["entry.*"], "track.read")     # False
        _action_matches(["track.read"], "track.read")  # True
    """
    for pa in policy_actions:
        if pa == "*" or pa == requested_action:
            return True
        if pa.endswith(".*") and requested_action.startswith(pa[:-1]):
            return True
    return False


def _filter_matches(policy: "Policy", resource: Resource) -> Tuple[bool, str]:
    """Resource-filter matching at evaluate-time (CONTEXT D-04).

    ``policy.entry_types`` empty = match any ``entry_type``; non-empty =
    ``resource.entry_type`` MUST be in the list.

    ``policy.tags`` empty = match any tags; non-empty = at least one entry of
    ``resource.tags`` MUST appear in ``policy.tags`` (any-intersection).

    Returns ``(matched, reason)``. ``reason`` is ``""`` on match,
    ``"filter_mismatch:entry_type"`` or ``"filter_mismatch:tags"`` on miss —
    matches the Decision.reason convention used in ``schemas/policy.py``.
    """
    if policy.entry_types:
        if resource.entry_type is None or resource.entry_type not in policy.entry_types:
            return (False, "filter_mismatch:entry_type")
    if policy.tags:
        if not any(t in policy.tags for t in resource.tags):
            return (False, "filter_mismatch:tags")
    return (True, "")


def _scope_matches(policy_scope: str, resource_scope: str) -> bool:
    """Scope match — exact equality OR ``policy_scope == "*"`` (wildcard).

    Per CONTEXT D-04: scope strings are typed (e.g. ``"track:<id>"``,
    ``"app:<id>"``, ``"user:<id>"``, ``"org:<id>"``). The ``"*"`` wildcard
    matches any. No prefix-match for v1 — exact-or-wildcard only — to avoid
    accidental ``track:`` matching ``track:t-1`` collisions.
    """
    if policy_scope == "*":
        return True
    return policy_scope == resource_scope


async def _find_policies_for_subject(subject: Subject) -> List["Policy"]:
    """Walk HAS_POLICY edges from the subject Node; return active Policies only.

    Phase 3 Plan 03 — replaces Plan 01's fail-closed-no-policy stub. Returns an
    empty list when no Policies are attached OR all attached Policies have
    ``is_active=False``. The caller (``evaluate``) interprets an empty list as
    ``fail_closed_no_policy``.

    Runtime import of ``list_policies_for_subject`` keeps the policy_registry
    module dependency out of the policy_engine module top-level import block —
    consistent with the Plan 01 import contract
    (``from app.services.policy_engine import evaluate`` must not require
    importing ``Policy`` or ``policy_registry`` to succeed).
    """
    from app.services.policy_registry import list_policies_for_subject

    policies = await list_policies_for_subject(subject.kind, subject.id)
    return [p for p in policies if p.is_active]


_SHARING_KIND_PREFIXES = frozenset({"app", "space", "track", "entry"})
_COLLABORATOR_MANAGE_SUFFIXES = frozenset(
    {
        "collaborator_add",
        "collaborator_remove",
        "collaborator_role_update",
    }
)
_SHARING_OWNER_SUFFIXES = frozenset(
    {
        "share_link.mint",
        "share_link.revoke",
        "invitation_create",
        "exclusion_add",
        "exclusion_remove",
    }
)
_PREFIX_TO_RESOURCE_KIND = {
    "app": "app",
    "space": "app",
    "track": "track",
    "entry": "entry",
}


async def _evaluate_sharing_human_action(
    action: str, resource: Resource, user_id: str
) -> Optional[bool]:
    """Dispatch sharing/collaboration PolicyActions via ``resolve_role``.

    Returns True/False when ``action`` is handled, or None to fall through.
    """
    if "." not in action:
        return None
    prefix, suffix = action.split(".", 1)
    if prefix == "workspace" and suffix in (
        "invitation_create",
        "invitation_revoke",
        "invitation_accept",
        "invitation_decline",
    ):
        wid = resource.id if resource.kind == "workspace" else ""
        scope_str = resource.scope or ""
        if not wid and scope_str.startswith("workspace:"):
            wid = scope_str.split(":", 1)[1]
        if not wid:
            return False
        from app.services.workspace_permissions import can_access_workspace

        ws_role = await can_access_workspace(user_id, wid)
        if suffix in ("invitation_accept", "invitation_decline"):
            return True
        if suffix in ("invitation_create", "invitation_revoke"):
            return ws_role in ("owner", "admin")
        return False

    if prefix not in _SHARING_KIND_PREFIXES:
        return None
    rk = _PREFIX_TO_RESOURCE_KIND[prefix]
    rid = resource.id
    if not rid:
        # Scoped actions (e.g. ``entry.create`` with ``scope=track:<id>``) are
        # handled by ``_evaluate_human_default`` — only owner-gated mutations
        # without a resource id should hard-deny here.
        if suffix in _SHARING_OWNER_SUFFIXES:
            return False
        return None
    if suffix in _COLLABORATOR_MANAGE_SUFFIXES:
        role = await resolve_role(user_id, rk, rid)
        return role in ("owner", "admin")
    if suffix in _SHARING_OWNER_SUFFIXES:
        return (await resolve_role(user_id, rk, rid)) in ("owner", "admin")
    if suffix == "share_link.redeem":
        return True
    return None


async def _evaluate_human_default(
    action: str, resource: Resource, user_id: str
) -> bool:
    """Default human policy — delegates to existing can_view_*/can_edit_* helpers.

    Per CONTEXT D-03: replicates the existing _user_permitted_for_scope decision
    tree at audit_log.py:67-83 / event_subscription_registry.py:103. This is the
    POL-03 parity gate: the 37-test CRUD regression suite passes UNCHANGED.

    Action mapping:

      track.read          → can_view_track
      track.update        → can_admin_track   (track-config authority —
                                              entry types, views, tags,
                                              CP mutations, anchors,
                                              migrations. Editors NOT
                                              granted; admins+owner only.)
      track.delete        → can_delete_track
      app.read            → can_view_app
      app.update          → can_admin_app    (App-config authority —
                                              attached CP + template-track
                                              mutations. Editors NOT granted.)
      app.delete          → can_delete_app
      entry.read          → can_view_entry
      entry.update        → can_edit_entry
      entry.delete        → can_edit_entry  (delete gate matches edit gate)
      entry.create        → can_edit_track  (entry creation requires
                                              track-edit access — editor+;
                                              resource.scope MUST be
                                              ``track:<parent_track_id>``)
      view.read           → can_view_view
      view.update         → can_edit_view   (admin-only via can_admin_track)
      view.delete         → can_edit_view
      audit_log.read      → can_view_track / can_view_app / identity
                            (matches Phase 2 _user_permitted_for_scope decision
                            tree at audit_log.py:67-83)
      event_feed.subscribe → same as audit_log.read

    For other resource kinds (tags, operational_models, comments, attachments,
    entry_types) the engine dispatches on ``resource.scope`` prefix
    (``track:`` / ``app:`` / ``user:``) — humans see anything in a track they
    can view, and mutations cascade through the parent track's edit gate.
    """
    rk = resource.kind
    rid = resource.id
    scope_str = resource.scope or ""

    # Audit-log / event-feed scope dispatch — mirrors Phase 2 _user_permitted_for_scope.
    if action in ("audit_log.read", "event_feed.subscribe"):
        if scope_str.startswith("track:"):
            return await can_view_track(user_id, scope_str.split(":", 1)[1])
        if scope_str.startswith("app:"):
            return await can_view_app(user_id, scope_str.split(":", 1)[1])
        if scope_str.startswith("user:"):
            scope_user_id = scope_str.split(":", 1)[1]
            if scope_user_id == user_id:
                return True
            # Phase 5 Plan 05-05 — accept the canonical User Node id form too.
            # Sync runtime + auth.py + operational_models emit events with
            # scope=f"user:{user_node.id}"; the request principal id may be
            # the AuthUser id rather than the User Node id. Resolve both
            # directions so the owner can read events tagged with either form.
            try:
                caller_user_node = await get_user_node(user_id)
            except Exception:  # noqa: BLE001 — defensive
                caller_user_node = None
            if (
                caller_user_node is not None
                and getattr(caller_user_node, "id", None) == scope_user_id
            ):
                return True
            return False
        return False  # unknown scope shape → defensive deny

    sharing_decision = await _evaluate_sharing_human_action(action, resource, user_id)
    if sharing_decision is not None:
        return sharing_decision

    # Resource-kind dispatch — track/app/entry/view handled by their own
    # can_view_*/can_edit_*/can_delete_* helpers.
    if rk == "track":
        if action == "track.read":
            return await can_view_track(user_id, rid)
        if action == "track.update":
            return await can_admin_track(user_id, rid)
        if action == "track.delete":
            return await can_delete_track(user_id, rid)
    if rk == "app":
        if action == "app.read":
            return await can_view_app(user_id, rid)
        if action == "app.update":
            return await can_admin_app(user_id, rid)
        if action == "app.delete":
            return await can_delete_app(user_id, rid)
    if rk == "entry":
        if action == "entry.read":
            return await can_view_entry(user_id, rid)
        if action == "entry.update" or action == "entry.delete":
            return await can_edit_entry(user_id, rid)
        if action == "entry.create":
            # Entry creation gates on the parent Track's edit permission. The
            # resource doesn't yet exist, so the caller passes the parent track
            # id via resource.scope ("track:<id>").
            if scope_str.startswith("track:"):
                return await can_edit_track(user_id, scope_str.split(":", 1)[1])
            return False
    if rk == "view":
        if action == "view.read":
            return await can_view_view(user_id, rid)
        if action == "view.update" or action == "view.delete":
            return await can_edit_view(user_id, rid)

    # Phase 3.1 Plan 03.1-03 — anchor.* dispatch for the default-human path.
    # The locked governance scope format is ``anchor_field:<cp_id>:<et_key>:<field_key>``
    # which doesn't map to a ``track:``/``app:``/``user:`` scope prefix; the
    # human-default permission gate is "can the caller edit the parent track
    # the anchor would attach to". For ``anchor.create`` + ``anchor.cascade``
    # + ``anchor.delete``, the engine consults can_edit_track on the parent
    # track id encoded by the caller in resource.id (when supplied) — or
    # falls through to scope-prefix dispatch for backward compatibility.
    #
    # Governance Policies (subject_kind="system") persisted by
    # materialize_governance_policies_for_operational_model DO NOT participate
    # in the default-human path — system Policies are out-of-band attestations
    # that govern agentive subjects (see policy_registry helper docstring).
    # CONTEXT decisions Governance subject (ANC-03) locks subject_kind="system"
    # and Phase 2 D-10 ActorKind invariant.
    if action in ("anchor.create", "anchor.cascade", "anchor.delete"):
        # Caller convention: resource.id is the parent Track id when known,
        # otherwise resource.scope carries ``track:<id>`` or ``anchor_field:<cp>:<et>:<field>``.
        # Anchors are track-config substrate — admin/owner only.
        if scope_str.startswith("track:"):
            return await can_admin_track(user_id, scope_str.split(":", 1)[1])
        if rid:
            return await can_admin_track(user_id, rid)
        # anchor_field scope alone — defensive deny (human callers should
        # always thread the parent Track id through resource.id or scope).
        return False

    # Phase 5 Plan 05-02 — migration.publish + migration.force_publish dispatch
    # for the default-human path. The publish endpoint at
    # api/operational_models.py already gates CP edit permission via
    # ``_resolve_cp_edit_permission`` (which itself delegates to the parent
    # Track/App's edit gate); the policy_engine call is a second-tier check.
    # Default-human ALLOWS both actions for any user who can edit the CP —
    # selective DENY of ``migration.force_publish`` is the privileged path
    # (Policy.subject_kind="human" with action=["migration.force_publish"]
    # + is_active=True + a deny semantic on a future Phase 6 reject-Policy
    # surface). Per the v1 substrate the default-human path mirrors the
    # endpoint-level edit gate so the existing CP draft/publish regression
    # suite continues to pass unchanged.
    if action in ("migration.publish", "migration.force_publish", "migration.run"):
        from app.models.nodes import OperationalModel as _OperationalModel

        cp = await _OperationalModel.get(rid)
        if cp is None:
            return False
        # If the resource id pointed at the draft, resolve the published
        # parent first — draft is not attached to any Track/App; the
        # parent is what the endpoint actually publishes onto.
        if getattr(cp, "status", "") == "draft" and getattr(cp, "draft_of_id", None):
            parent = await _OperationalModel.get(cp.draft_of_id)
            if parent is not None:
                cp = parent
        # Library packages — caller needs publish rights under the CP's
        # workspace (mirrors api/operational_models._resolve_cp_edit_permission).
        if getattr(cp, "library_package", False):
            from app.services.permissions import (
                can_publish_operational_models_under_workspace as _can_publish,
            )

            return await _can_publish(user_id, cp.workspace_id)
        # Reuse the same gate the publish endpoint already passes through:
        # for track-scope, the user must be able to edit the owning Track;
        # for app-scope, the owning App.
        if cp.scope == "track":
            from app.models.nodes import Track as _Track

            owner_tracks = await _Track.find(
                {"context.attached_operational_model_id": cp.id}
            )
            for t in owner_tracks:
                if await can_edit_track(user_id, t.id):
                    return True
            return False
        if cp.scope == "app":
            from app.models.nodes import App as _Space

            owner_spaces = await _Space.find(
                {"context.attached_operational_model_id": cp.id}
            )
            for sp in owner_spaces:
                if await can_edit_app(user_id, sp.id):
                    return True
            return False
        return False

    # (The A2A ``agent.discover`` / ``a2a.delegate`` default-human dispatch
    # branches were retired with the agent-to-agent fabric — ADR-003.)

    # Phase 6 Plan 06-01 — operational_model.author default-human dispatch
    # (06-PLAN-CHECK.md BLOCKER #1 fix). Plan 06-04's POST
    # /api/operational-models/author calls
    # ``policy_engine.evaluate(subject=Subject(kind="human"), action="operational_model.author")``
    # as the Tier 1 gate; without this branch human callers fail-close
    # at the engine. Delegate to ``can_publish_operational_models_under_workspace``
    # for ``workapp:<id>`` scope — collapses Tier 1 to the same gate the
    # endpoint already uses. Track-/app-scope authoring isn't a v1 use case
    # (CP packages are workspace-keyed); deny defensively for unknown scopes.
    if action == "operational_model.author":
        if scope_str.startswith("workspace:"):
            from app.services.permissions import (
                can_publish_operational_models_under_workspace,
            )

            return await can_publish_operational_models_under_workspace(
                user_id, scope_str.split(":", 1)[1]
            )
        return False

    # Resource kinds where read access cascades through scope (tags,
    # operational_models, comments, attachments, entry_types) — humans see
    # anything in a track they can view.
    if action.endswith(".read") or action == "event_feed.subscribe":
        # An entry-scoped read resolves on the ENTRY. Its scope still names the
        # parent track (that is how the cascade is expressed), but an
        # ``EXCLUDED_FROM`` edge on the entry itself is invisible to a
        # track-level check — see the comment tier below, where the same gap
        # let an excluded user post.
        if rk == "entry" and rid:
            return await can_view_entry(user_id, rid)
        if scope_str.startswith("track:"):
            return await can_view_track(user_id, scope_str.split(":", 1)[1])
        if scope_str.startswith("app:"):
            return await can_view_app(user_id, scope_str.split(":", 1)[1])
        if scope_str.startswith("user:"):
            return scope_str.split(":", 1)[1] == user_id

    # Resource kinds where mutation cascades through scope.
    #
    # Three-tier gate per the admin/editor/commenter split:
    #   - comment.* / reaction.*  → commenter+ (read + participate, no edits),
    #     EXCEPT the moderation verbs below, which are admin-tier
    #   - comment.moderate        → can_admin_track (remove another's comment)
    #   - entry.create / entry.update / entry.delete
    #     → can_edit_track (editor+ allowed)
    #   - everything else (tags.*, operational_models.*, entry_types.*, views.*,
    #     anchor.*, migration.*, attachments.*, ...) → can_admin_track
    #     (admin/owner only; editors do NOT mutate track-config substrate)
    #
    # Comments + reactions sit one tier BELOW entry mutation: the whole point
    # of the `commenter` role is read + post comments with no entry-edit
    # rights, so they must not be gated on can_edit_*, which starts at editor.
    # These used to ride the entry tier while the comment endpoints carried
    # their own ROLE_RANK >= commenter check; the gate now lives here only.
    # Moderation is the one comment-namespaced action that does NOT ride the
    # commenter tier. Removing somebody else's words is a governance act, not
    # participation: on the commenter tier every commenter could delete every
    # other person's comments. It sits at the admin tier with the rest of the
    # track-governance surface. Matched exactly, before the prefix test below,
    # because ``"comment.moderate".startswith("comment.")`` is true — a new
    # governance verb in this namespace must be added here too, or it silently
    # inherits commenter+.
    _MODERATION_ACTIONS = frozenset({"comment.moderate"})
    _ENTRY_TIER_PREFIXES = ("entry.",)
    _COMMENT_TIER_PREFIXES = ("comment.", "reaction.")

    def _is(prefixes: tuple) -> bool:
        return action not in _MODERATION_ACTIONS and any(
            action.startswith(p) for p in prefixes
        )

    async def _has_comment_rank(resource_type: str, resource_id: str) -> bool:
        role = await resolve_role(user_id, resource_type, resource_id)
        return ROLE_RANK.get(role or "", 0) >= ROLE_RANK["commenter"]

    if scope_str.startswith("track:"):
        track_id = scope_str.split(":", 1)[1]
        if _is(_COMMENT_TIER_PREFIXES):
            # Resolve on the ENTRY when the action targets one. The scope names
            # the parent track because that is how the cascade is expressed,
            # but resolving there skips per-ENTRY ``EXCLUDED_FROM``: a user
            # excluded from a single entry still resolved as the track's
            # editor and could POST a comment on it — while GET on the same
            # entry already refused them (``entry.read`` → ``can_view_entry``,
            # which does honour the edge). Read denied, write allowed, on the
            # same row.
            #
            # ``resolve_role(entry)`` cascades to the track and on to the App,
            # so every legitimate path in still resolves; what changes is that
            # the deny edge is no longer invisible.
            if rk == "entry" and rid:
                return await _has_comment_rank("entry", rid)
            return await _has_comment_rank("track", track_id)
        if _is(_ENTRY_TIER_PREFIXES):
            return await can_edit_track(user_id, track_id)
        return await can_admin_track(user_id, track_id)
    if scope_str.startswith("app:"):
        app_id = scope_str.split(":", 1)[1]
        if _is(_COMMENT_TIER_PREFIXES):
            if rk == "entry" and rid:
                return await _has_comment_rank("entry", rid)
            return await _has_comment_rank("app", app_id)
        if _is(_ENTRY_TIER_PREFIXES):
            return await can_edit_app(user_id, app_id)
        return await can_admin_app(user_id, app_id)
    if scope_str.startswith("user:"):
        return scope_str.split(":", 1)[1] == user_id

    # Phase 5 Plan 05-05 — connector:<id> scope dispatch for humans.
    # Owner of the Connector may invoke ``connector.sync`` + read its
    # audit-trail events; without this branch ``POST /api/connectors/{id}/sync``
    # is unreachable by any human caller (the per-connector Policy
    # materialized in Plan 05-01 covers the CONNECTOR subject, not humans).
    # Owner check uses ``Connector.owner`` scalar (Phase 1 D-07) which carries
    # the User Node id; the request principal id may be either the User Node
    # id OR the AuthUser id, so we resolve through ``get_user_node`` to find
    # the canonical User Node id (matching the pattern used by can_view_*
    # / can_edit_* helpers elsewhere in this module).
    if scope_str.startswith("connector:"):
        connector_id = scope_str.split(":", 1)[1]
        try:
            from app.agentive.nodes import Connector as _Connector

            _connector = await _Connector.get(connector_id)
            if _connector is None:
                return False
            caller_user_node = await get_user_node(user_id)
            caller_user_node_id = (
                getattr(caller_user_node, "id", None) if caller_user_node else None
            )
            owner = getattr(_connector, "owner", "")
            if owner and (owner == user_id or owner == caller_user_node_id):
                return True
            return False
        except Exception:  # noqa: BLE001 — defensive: any failure → deny
            return False

    return False  # defensive deny


def _is_write_action(action: str) -> bool:
    """Phase 7 Plan 07-04 — classify a PolicyAction as a write or a read.

    Used by the ``requires_human_approval`` intercept to gate writes only.
    Read actions are never intercepted (read intercept would deadlock the
    approval workflow itself — the review UI lists pending writes via reads).

    True for any action whose suffix indicates a mutation:
      .create / .update / .delete / .merge_library / .publish / .author /
      .resolve / .modify / .force_publish / .approve / .reject / .run /
      .start / .complete / .failed / .heartbeat / .register / .verify /
      .accept / .decline / .revoke / .expire / .add / .remove / .mint /
      .redeem / .deny / .cascade / .sync / .delegate / .discover

    False for:
      .read / .subscribe (the only two read action suffixes in the v1
      PolicyAction Literal)

    Future expansion: when PolicyAction grows new SUFFIXES, update both this
    helper and the matching tests. The helper is intentionally conservative
    — unknown suffixes default to True (write semantics) so the intercept
    fires by default; whitelist read variants explicitly.
    """
    # Read whitelist — never intercepted regardless of policy flags.
    READ_SUFFIXES = (".read", ".subscribe")
    for suffix in READ_SUFFIXES:
        if action.endswith(suffix):
            return False
    # Gate-only read-class actions (no mutation) — exempt from the write
    # intercept.
    READ_FULL = ("audit_log.read",)
    if action in READ_FULL:
        return False
    return True


async def evaluate(
    *,
    subject: Subject,
    action: str,
    resource: Resource,
    payload: Optional[Dict[str, Any]] = None,
    _internal_actor: Optional[Subject] = None,
    _dry_run: bool = False,
) -> Decision:
    """Single authorization entry point — POL-01 contract.

    Per CONTEXT D-10: ``_internal_actor`` is a private recursion guard. When set
    to ``Subject(kind="system", id="policy_engine")``, evaluate short-circuits to
    allow regardless of ``subject``. NEVER expose ``_internal_actor`` outside
    this module — it is reserved for the Plan 05 denial-emit path so
    ``policy_engine.evaluate → emit_change_event → ... → policy_engine.evaluate``
    does not infinite-recurse.

    ``_dry_run`` (F1 explain API): compute the Decision without mutating
    (no Approval create, no ``policy.deny`` emit) and without reading/writing
    the per-request decision cache — so forensic explain cannot poison a
    subsequent real authorize in the same request.

    Decision shape (POL-04 observability):

      reason="system_subject_internal"   — recursion-guard short-circuit
      reason="system_subject"             — subject.kind == "system"
      reason="default_human_policy"       — human allowed via legacy helper
      reason="default_human_policy_denied"— human denied via legacy helper
      reason="fail_closed_no_policy"      — agent/connector w/ no Policy edge
                                            (Plan 03 extends this branch with
                                            HAS_POLICY graph traversal)
      reason="requires_human_approval"    — Phase 7 Plan 07-04 — agent write
                                            intercepted, Approval persisted;
                                            ``approval_id`` is set on the
                                            returned Decision.

    Phase 7 Plan 07-04 ``payload`` kwarg:
        When provided, used for two purposes by the requires_human_approval
        intercept: (1) persisted into ``Approval.payload`` so the approve
        path can re-run the original write, and (2) size-checked against
        ``APPROVAL_PAYLOAD_MAX_BYTES`` (default 1MB). Read actions pass
        ``payload=None``; mutation handlers MUST pass the request body so a
        re-run on approve replays the agent's original intent.
    """
    # Recursion guard (D-10) — the denial-emit path in Plan 05 sets _internal_actor.
    if _internal_actor is not None and _internal_actor.kind == "system":
        return Decision(allowed=True, reason="system_subject_internal")

    # System subject bypass (D-03) — reserved for internal background jobs.
    if subject.kind == "system":
        return Decision(allowed=True, reason="system_subject")

    # Per-request cache lookup (D-11). Dry-run skips cache entirely.
    cache = permissions_cache_get()
    cache_key = (
        "policy",
        subject.kind,
        subject.id,
        action,
        resource.kind,
        resource.id,
    )
    if not _dry_run and cache_key in cache:
        return cache[cache_key]

    decision: Decision

    if subject.kind == "human":
        # Default-human path — DELEGATES to existing helpers (POL-03 parity).
        # See RESEARCH §"Pitfall 1": NEVER reimplement precedence rules here.
        allowed = await _evaluate_human_default(action, resource, subject.id)
        decision = Decision(
            allowed=allowed,
            reason="default_human_policy" if allowed else "default_human_policy_denied",
        )
    else:
        # Agents and connectors — graph-local Policy lookup via HAS_POLICY edges
        # (Plan 03-03). Per CONTEXT D-04: fail-closed when no active matching
        # Policy exists. Iteration order is HAS_POLICY edge insertion order
        # (jvspatial-defined); the engine evaluates each in turn and returns
        # on the first allow.
        policies = await _find_policies_for_subject(subject)
        if not policies:
            decision = Decision(
                allowed=False,
                reason="fail_closed_no_policy",
                policy_chain=[],
            )
        else:
            policy_chain: List[str] = []
            matched: Optional["Policy"] = None
            mismatch_reason: str = ""
            for p in policies:
                policy_chain.append(p.id)
                if not _scope_matches(p.scope, resource.scope):
                    mismatch_reason = mismatch_reason or "scope_mismatch"
                    continue
                if not _action_matches(p.actions, action):
                    mismatch_reason = mismatch_reason or "action_mismatch"
                    continue
                ok, fr = _filter_matches(p, resource)
                if not ok:
                    mismatch_reason = mismatch_reason or fr
                    continue
                matched = p
                break
            if matched is not None:
                # Phase 7 Plan 07-04 — requires_human_approval intercept.
                # Gated on agent + write + flag-set (CONTEXT lock #6 + #9).
                # Read intercept would deadlock the approval workflow (UI
                # lists pending writes via reads). Human callers reach this
                # branch via the agent-policy graph traversal only if a
                # Policy is attached to them — same logic applies (the flag
                # is generally only set on agent subjects, but the engine
                # guards explicitly on subject.kind == 'agent' to match the
                # documented CONTEXT scope).
                if (
                    bool(getattr(matched, "requires_human_approval", False))
                    and _is_write_action(action)
                    and subject.kind == "agent"
                ):
                    if _dry_run:
                        # Explain path — report intercept without minting
                        # an Approval node.
                        decision = Decision(
                            allowed=False,
                            reason="requires_human_approval",
                            matched_policy_id=matched.id,
                            policy_chain=policy_chain,
                            approval_id=None,
                        )
                    else:
                        # Lazy import to avoid models/services circular import
                        # at module load. Approval lives in app.models.nodes;
                        # keeping the import lazy for symmetry with the existing
                        # matched-policy import pattern.
                        from app.models.nodes import Approval

                        # Payload size cap (1MB default). PayloadTooLargeError ->
                        # HTTP 413 envelope.
                        try:
                            max_bytes = int(
                                os.getenv("APPROVAL_PAYLOAD_MAX_BYTES", "1048576")
                            )
                        except ValueError:
                            max_bytes = 1048576
                        payload_serialized = (
                            json.dumps(payload) if payload is not None else ""
                        )
                        if len(payload_serialized) > max_bytes:
                            raise PayloadTooLargeError(
                                message=(
                                    "Payload exceeds approval cap "
                                    f"({len(payload_serialized)} > {max_bytes} bytes)"
                                )
                            )

                        # TTL — default 7 days.
                        try:
                            ttl_days = int(os.getenv("APPROVAL_TTL_DAYS", "7"))
                        except ValueError:
                            ttl_days = 7
                        now_iso = utc_now_iso()
                        try:
                            now_dt = datetime.fromisoformat(now_iso)
                        except ValueError:
                            now_dt = utc_now()
                        expires_at = (now_dt + timedelta(days=ttl_days)).isoformat()

                        ap = await Approval.create(
                            actor_kind=subject.kind,
                            actor_id=subject.id,
                            action=action,
                            resource_kind=resource.kind,
                            resource_id=resource.id or "",
                            payload=payload or {},
                            policy_id=matched.id,
                            status="pending",
                            created_at=now_iso,
                            expires_at=expires_at,
                        )
                        # Phase 10.5 Plan 10.5-05 (I-GRAPH-01): wire
                        # Policy -HAS_APPROVAL-> Approval so the deferred
                        # write surfaces under the originating Policy
                        # subgraph (which itself is reachable via
                        # subject -HAS_POLICY-> Policy when subject is
                        # rooted). Hard-fail + delete orphan on wire error.
                        from app.models.edges import HAS_APPROVAL

                        try:
                            await matched.connect(
                                ap, edge=HAS_APPROVAL, created_at=now_iso
                            )
                        except Exception as exc:
                            logger.exception(
                                "policy_engine: HAS_APPROVAL wire failed for "
                                "policy=%s approval=%s",
                                matched.id,
                                ap.id,
                            )
                            try:
                                await ap.delete()
                            except Exception:
                                logger.exception(
                                    "policy_engine: rollback delete failed "
                                    "approval=%s",
                                    ap.id,
                                )
                            raise RuntimeError(
                                f"HAS_APPROVAL wire failed for policy={matched.id} "
                                f"approval={ap.id}"
                            ) from exc
                        decision = Decision(
                            allowed=False,
                            reason="requires_human_approval",
                            matched_policy_id=matched.id,
                            policy_chain=policy_chain,
                            approval_id=ap.id,
                        )
                else:
                    decision = Decision(
                        allowed=True,
                        reason=f"policy_match:{matched.id}",
                        matched_policy_id=matched.id,
                        policy_chain=policy_chain,
                    )
            else:
                decision = Decision(
                    allowed=False,
                    reason=mismatch_reason or "no_matching_policy",
                    policy_chain=policy_chain,
                )

    if not _dry_run:
        cache[cache_key] = decision

    # POL-04 observability: every denial is structurally logged + audit-emitted
    # via the single emission helper (Phase 2 D-05 path). Allow decisions do
    # NOT emit (CONTEXT D-15 T-04 disposition — denial-only audit at v1 to
    # avoid log flooding under per-list-iteration permission checks).
    #
    # Stdout log level is tiered: hot-path denials that occur on every
    # broadcast / list iteration (e.g. `event_feed.subscribe` runs per
    # subscriber per broadcast; `fail_closed_no_policy` fires for every
    # un-policied agent on every request) are DEBUG-level — the persistent
    # audit ChangeEvent emission (suppressed from WS via BROADCAST_SKIP_ACTIONS)
    # is the durable record. Substantive denials (write actions, anchor
    # cascade, profile author/modify, etc.) remain INFO. Without this tier,
    # an idle dev server emits ~2 deny lines per WS broadcast attempt and
    # tens per minute under load. The ChangeEvent audit trail is unaffected.
    # Dry-run (F1 explain) skips logging + emit so forensic reads stay pure.
    if not decision.allowed and not _dry_run:
        # Hot-path actions deny on every broadcast / list iteration —
        # `event_feed.subscribe` runs per subscriber per broadcast (see
        # `services/event_subscription_registry._user_permitted_for_scope`),
        # `entry.read`/`track.read`/`app_node.read` run per-item on list endpoints.
        # Each denial-log line on these actions is expected operational noise;
        # the persistent audit ChangeEvent (suppressed from WS broadcast via
        # BROADCAST_SKIP_ACTIONS) is the durable record. DEBUG keeps the
        # signal available to operators who want it without polluting stdout
        # under normal load. Substantive denials (write actions, anchor.*,
        # profile.*, fail_closed_no_policy on first agent contact, etc.)
        # remain INFO.
        _hot_path_action = action in {
            "event_feed.subscribe",
            "entry.read",
            "track.read",
            "app.read",
        }
        _log_level = logger.debug if _hot_path_action else logger.info
        _log_level(
            "policy_engine.deny",
            extra={
                "subject_kind": subject.kind,
                "subject_id": subject.id,
                "action": action,
                "resource_kind": resource.kind,
                "resource_id": resource.id,
                "scope": resource.scope,
                "decision_reason": decision.reason,
                "matched_policy_id": decision.matched_policy_id,
            },
        )

        # Denial-emit — write a policy.deny ChangeEvent via the single emission
        # helper. The recursion-guard kwarg `_internal_actor` is threaded
        # through per CONTEXT D-10 invariant: any future code path that adds
        # `policy_engine.evaluate(...)` inside `emit_change_event` (or inside
        # anything it transitively invokes — best-effort EmittedBy writes, WS
        # broadcast filters, etc.) automatically receives the system-actor
        # short-circuit via the kwarg already on the call. Plan 01 Test 21
        # verifies the engine short-circuits on
        # Subject(kind="system", id="policy_engine") returning
        # Decision(allowed=True, reason="system_subject_internal").
        try:
            await emit_change_event(
                actor_kind=subject.kind,
                actor_id=subject.id,
                action="policy.deny",
                resource_type=resource.kind,
                resource_id=resource.id,
                before=None,
                after=None,
                scope=resource.scope,
                details={
                    "failed_action": action,
                    "decision_reason": decision.reason,
                    "matched_policy_id": decision.matched_policy_id,
                },
                # D-10 recursion guard — engine bypasses re-evaluation when
                # called with _internal_actor=Subject(kind="system", ...). The
                # kwarg is in-process control flow only; emit_change_event
                # does NOT persist it to the DBLog row.
                _internal_actor=Subject(kind="system", id="policy_engine"),
            )
        except Exception as exc:
            # Defensive — the engine's primary job is to return a Decision.
            # An audit-emit failure must NEVER flip a deny into an allow or
            # propagate to the caller. Log a WARNING and continue.
            logger.warning(
                "policy_engine.evaluate: denial-emit failed (decision returned anyway): %s",
                exc,
            )

    return decision
