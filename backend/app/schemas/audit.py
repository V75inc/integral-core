"""ChangeEvent + Actor Pydantic schemas — wire shape for the audit log + event feed.

PROV-03 + PROV-04 + EVT-01 + EVT-02: schema mirrors Phase 1's typed-boundary convention.
The jvspatial Node `ChangeEvent` (in app/models/nodes.py) is the persistence shape;
`ChangeEventResponse` here is the API/WS broadcast shape.

Per D-09: ChangeEventAction is a locked Literal — free-string fallback rejected.
Per D-10: Actor.kind reuses ActorKind from app.schemas.provenance (single source of truth).

Mutation-surface audit (per PLAN 02-02 Sub-task 1a) — every (file, function, action)
mapped to a ChangeEventAction Literal member below:
    api/entries.py::create_entry            -> entry.create
    api/entries.py::update_entry            -> entry.update
    api/entries.py::delete_entry            -> entry.delete
    api/entries.py::add_tag_to_entry        -> entry.update
    api/entries.py::remove_tag_from_entry   -> entry.update
    api/entries.py::add_reaction            -> entry.update
    api/entries.py::remove_reaction         -> entry.update
    api/tracks.py::create_track             -> track.create
    api/tracks.py::patch_track_content_profile           -> content_profile.update
    api/tracks.py::merge_library_into_track_content_profile -> content_profile.merge_library
    api/tracks.py::update_track             -> track.update
    api/tracks.py::delete_track             -> track.delete
    api/tracks.py::add_collaborator         -> track.update
    api/tracks.py::remove_collaborator      -> track.update
    api/tracks.py::post_transfer_track_ownership         -> track.update
    api/apps.py::(create/update/delete + many sub-routes)    -> app.* / content_profile.*
    api/content_profiles.py::(library + attached mutators)   -> content_profile.*
    api/tags.py::(create/update/delete)     -> tag.create / tag.update / tag.delete
    api/views.py::(create/update/delete)    -> view.create / view.update / view.delete
    api/apps_dashboards.py::(create/patch/delete) -> dashboard.create / dashboard.update / dashboard.delete
    api/comments.py::(create/update/delete) -> comment.create / comment.update / comment.delete
    api/attachments.py::upload              -> attachment.create
    api/attachments.py::delete              -> attachment.delete
    api/users.py::(create/update/delete)    -> user.create / user.update / user.delete
    api/notifications.py::create_notification         -> notification.create
    api/notifications.py::mark_notification_as_read   -> notification.read           (Phase 9 Plan 09-02)
    api/notifications.py::mark_all_notifications_as_read -> notification.mark_all_read (Phase 9 Plan 09-02)
    services/prompt_queue.py::resolve_question_item   -> prompt_queue.resolve_question
    services/prompt_queue.py::mark_write_item         -> prompt_queue.mark_write
    services/prompt_queue.py::cancel_all              -> prompt_queue.cancel_all
    api/workspaces.py::(create/update/delete + member_add/remove)
                                            -> workspace.create / .update / .delete /
                                               .member_add / .member_remove
    api/feed.py::(create/update/delete)     -> entry.create / .update / .delete  (feed re-emits entry mutations)
    api/entry_types.py::(create/update/delete) -> entry_type.create / .update / .delete
    api/auth.py::update_profile             -> user.update
    agentive/api/uplink.py::register_agent  -> agent_config.register
    agentive/api/uplink.py::unregister_agent -> agent_config.update
    agentive/api/uplink.py::register_system_agent -> agent_config.register
    agentive/api/connectors.py::post_create_connector -> connector.create
    agentive/api/channels.py::(identity create/delete/verify) -> channel_identity.{create,delete,verify}
    agentive/api/proactive.py::patch_preferences -> agent_config.update

ALLOW-LISTED (no auditable graph mutation; see test_change_event_no_bypass.py):
    api/auth.py::signup, login, refresh, logout (auth handshake)
    api/notifications.py::respond_notification (audit deferred; ack-only)
    (Phase 9 Plan 09-02 closed mark_notification_as_read +
    mark_all_notifications_as_read — both now emit ChangeEvents directly.)
    api/attachments.py::create_attachment_from_url (presigned-style URL only)
    agentive/api/proactive.py::proactive_log_push, proactive_push (log-only)
    agentive/api/uplink.py::agent_heartbeat, agent_heartbeat_system (high-frequency)
    agentive/api/conversations.py::* (in-flight context, per-turn — non-audited per Phase 2 scope)
    agentive/api/chat.py::chat_message (chat turn dispatch — non-mutation)
    agentive/api/agent_tools.py::call_tool (tool dispatch facade)
    agentive/api/smart_file.py::create_entry_from_smart_file
        (delegates to entries.py::create_entry which already emits)
"""

from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel

from app.schemas.provenance import ActorKind  # D-10 shared Literal

ChangeEventAction = Literal[
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
    "content_profile.create",
    "content_profile.update",
    "content_profile.delete",
    "content_profile.merge_library",
    "content_profile.publish",
    # Admin-driven library-bundle rescan (POST /admin/profiles/rescan).
    # Emitted once per successful rescan with the added/updated/removed
    # diff + invalidated-workspaces detail. PolicyAction mirrors below.
    "content_profile.rescan",
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
    "attachment.create",
    "attachment.delete",
    "entry_type.create",
    "entry_type.update",
    "entry_type.delete",
    "notification.create",
    "notification.delete",
    # Phase 9 Plan 09-02 (NOTIF-01) — mark-read state transitions.
    # Additive extension; PolicyAction mirrors (notification.read already
    # exists in PolicyAction as the audit-log read-gate variant — same
    # string, dual role; notification.mark_all_read is mirrored fresh).
    # Single-Literal AST grep gate UNCHANGED — these are members of the
    # one existing ChangeEventAction definition. Closes 2 of 43 deferred
    # D-05 emission gaps (notifications.mark_notification_as_read +
    # notifications.mark_all_notifications_as_read).
    "notification.read",
    "notification.mark_all_read",
    # Prompt Sheet queue (ADR-003 one-audit-trail): answering a clarifying
    # question, resolving a staged write, and cancelling the queue all mutate
    # ChatThread.prompt_queue and were previously unaudited.
    "prompt_queue.resolve_question",
    "prompt_queue.mark_write",
    "prompt_queue.cancel_all",
    # Phase-1-aware agentive mutations
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
    # User / Org mutations (audited per Phase 2 scope)
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
    # Phase 3 Plan 03-03 — Policy CRUD audit actions (POL-02).
    # Strict-additive expansion, same idiom as Plan 02-02 Sub-task 1a.
    # PolicyAction in app/schemas/policy.py already includes these as a
    # strict-superset; the test_policy_action_strict_supersets_change_event_action
    # CI gate continues to pass after this extension.
    "policy.create",
    "policy.update",
    "policy.delete",
    # Phase 3 Plan 03-05 — engine-internal denial audit action (POL-04 + TEST-02).
    # Emitted by policy_engine.evaluate for every Decision(allowed=False) via the
    # single emit_change_event helper (Phase 2 D-05 path). Strict-superset
    # extension preserved — PolicyAction already supersets ChangeEventAction.
    "policy.deny",
    # Phase 3.1 Plan 03.1-03 — anchor association governance + cascade lifecycle
    # (ANC-03 + ANC-05). Three mutation actions + one denial action mirror the
    # policy.* precedent; PolicyAction lists all four so the strict-superset
    # invariant continues to hold. WS broadcast skip for anchor.cascade lives
    # in change_event.py (high-fan-out safeguard mirrors policy.deny skip).
    "anchor.create",
    "anchor.delete",
    "anchor.cascade",
    "anchor.deny",
    # Phase 5 Plan 05-01 additions (CONTEXT locked decision #5).
    # Connector + migration audit actions. PolicyAction strict-supersets
    # these (INVARIANTS.md L43-63 gate). ``migration.start`` is intentionally
    # OMITTED — covered by ``content_profile.publish`` (existing).
    "connector.sync.start",
    "connector.sync.complete",
    "connector.sync.failed",
    "migration.run",  # covers start + complete + failed via emit details.state
    # (The A2A delegation audit action ``a2a.delegate`` was retired with the
    # agent-to-agent fabric — ADR-003. ``profile.author`` is intentionally not
    # an audit action — authoring a CP already emits ``content_profile.create``.)
    # Phase 7 Plan 07-04 additions.
    # System-emitted ChangeEvent action — fires when the approval TTL reclaim
    # loop marks a row past its ``expires_at``. Audit-only — there is NO
    # PolicyAction twin (follows the ``policy.deny`` / ``anchor.deny``
    # precedent: caller never evaluates(action='approval.expired')).
    # The strict-superset invariant gate
    # ``test_policy_action_strict_supersets_change_event_action``
    # whitelists this member alongside the existing audit-only exemptions.
    "approval.expired",
    # ---- Phase 10 Plan 10-05 additions ----
    # App lifecycle audit actions. PolicyAction mirrors these in lockstep
    # (INVARIANTS.md L43-63 strict-superset gate / I-CHA invariant).
    # Each lifecycle action emits EXACTLY ONE ChangeEvent per D-05 — the
    # install transaction emits ``app.installed`` only at step 12 (final
    # active state); uninstall emits ``app.uninstalled`` once at the end
    # of the normal path or ``app.force_uninstalled`` once at the start
    # of the force path. The single-emission helper is
    # ``services/change_event.py::emit_change_event``.
    "app.installed",
    "app.uninstalled",
    "app.force_uninstalled",
    # Post-Phase-10 — App primitive CRUD audit actions. The Space → App
    # rename retained the historical ``space.*`` audit strings above so
    # pre-rename rows still parse, but new emissions all use ``app.*``.
    "app.create",
    "app.update",
    "app.delete",
    "app.read",
    # Sharing primitives — app resource (mirrors space.* above; new emissions use app.*).
    "app.collaborator_add",
    "app.collaborator_remove",
    "app.collaborator_role_update",
    "space.collaborator_role_update",
    "app.exclusion_add",
    "app.exclusion_remove",
    "app.share_link.mint",
    "app.share_link.revoke",
    "app.share_link.redeem",
    "app.invitation_create",
    # Lifecycle: entry archive (soft delete) + pending-write TTL expiry.
    "entry.archived",
    "pending_write.expired",
    # Reorder audit actions — emitted by workspace.reorder_apps and
    # app.reorder_tracks. Strict-superset gate
    # (test_policy_action_strict_supersets_change_event_action) mirrored in
    # PolicyAction.
    "workspace.apps_reorder",
    "app.tracks_reorder",
    # Tool dispatch audit (services/hooks/tool_dispatch.py::run_tool).
    # Single emission per tool invocation via ToolContext.emit_audit.
    # PolicyAction mirrors below.
    "tool.invoke",
    # BYOK model credential lifecycle (user settings).
    "model_credential.upsert",
    "model_credential.revoke",
    # Routine tasks (user-issued recurring chat instructions). PolicyAction
    # mirrors below; routine_task.read is PolicyAction-only (read actions
    # are never audited as ChangeEvents — see the Phase 3 read-action
    # precedent in policy.py).
    "routine_task.create",
    "routine_task.update",
    "routine_task.delete",
    "routine_task.run_completed",
    "routine_task.auto_paused",
    # Run-count dimension — emitted when a routine hits its max_runs cap.
    "routine_task.completed",
    # Workspace skills editor (skills-editor v1).
    "skill.customized",
    "skill.created",
    "skill.deleted",
    "skill.reset",
    "skill.toggled",
    # Agent chat rollback — emitted when a consumed staged change is undone.
    "staging.rollback",
]


class Actor(BaseModel):
    """Actor descriptor on every ChangeEvent (D-10)."""

    kind: ActorKind
    id: str

    model_config = {"extra": "forbid"}


class ChangeEventResponse(BaseModel):
    """Wire shape for /api/audit-log + WS broadcast (PROV-04, EVT-01)."""

    id: str
    ts: datetime
    actor: Actor
    action: ChangeEventAction
    resource_type: str
    resource_id: str
    scope: str  # "track:<id>" | "app:<id>" | "user:<id>"
    before: Optional[Dict[str, Any]] = None
    after: Optional[Dict[str, Any]] = None
    # Phase 3 Plan 03-05 — additive metadata slot, populated by policy.deny
    # events with {failed_action, decision_reason, matched_policy_id}.
    # Persisted in DBLog.log_data["details"]; surfaced to /api/audit-log.
    # Not subject to Phase 2 D-04 TTL reclaim — denial details are retained
    # indefinitely for forensic visibility.
    details: Optional[Dict[str, Any]] = None

    model_config = {"extra": "forbid"}
