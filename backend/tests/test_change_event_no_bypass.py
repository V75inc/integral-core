"""D-05 invariant: every mutation handler in app/api/ and app/agentive/api/ emits
a ChangeEvent. Enforces single-emission-path. AST-based to avoid false positives
on docstrings / comments.

Allow-list endpoints that legitimately do not mutate audited state (heartbeat,
auth handshake, search, log-push, notification mark-read, etc.).
Every entry has a justifying inline comment.
"""

from __future__ import annotations

import ast
from pathlib import Path

# (file_relative_path, function_name) tuples — handlers exempt from the rule.
# Every entry must have a justifying inline comment.
ALLOW_LIST: set[tuple[str, str]] = {
    # Phase 9 Plan 09-02 (NOTIF-01) — mark_notification_as_read and
    # mark_all_notifications_as_read now emit ChangeEvents
    # (notification.read + notification.mark_all_read respectively).
    # Their prior ALLOW_LIST entries were removed when D-05 single-emission
    # was wired into both handlers; do NOT re-add them.
    # respond_to_notification: pure ack channel (no resource state change
    # beyond mark-read; the mark-read transition is now emitted directly
    # via the dedicated PUT /notifications/{id}/read handler).
    ("backend/app/api/notifications.py", "respond_to_notification"),
    # Phase 9 Plan 09-03a (NOTIF-02 / D-05 / B6) — create_notification
    # delegates via notification_router.dispatch which routes through
    # InAppChannel.dispatch; the notification.create ChangeEvent is
    # emitted transitively from the channel adapter (single-emission
    # invariant). Same precedent as content_profiles.modify_content_profile
    # (delegates to publish_draft which emits). Grep gates in this plan's
    # acceptance_criteria assert 0 occurrences of the literal emit-action
    # string in api/notifications.py and 1 in
    # services/notification_channels/in_app_channel.py.
    ("backend/app/api/notifications.py", "create_notification"),
    # validation-only endpoint; returns parsed canonical manifest. No mutation.
    ("backend/app/api/content_profiles.py", "validate_content_profile_manifest"),
    # Phase 6 Plan 06-04 — modify_content_profile delegates to
    # publish_draft (Phase 3.1 atomic-swap) which emits the
    # content_profile.publish event transitively. Same precedent as
    # publish_content_profile_draft. (I-PROFILE-02.)
    ("backend/app/api/content_profiles.py", "modify_content_profile"),
    # Read-only manifest preview — returns the merged manifest WITHOUT persisting.
    # Renamed from preview_space_content_profile_merge during the Space → App
    # hard-cutover sweep (commit 92c4c69).
    ("backend/app/api/apps.py", "preview_app_content_profile_merge"),
    # (tags.py::create_tag was here for the same I-CRUD-01 reason as
    # entry_types.py::create_entry_type — the tag.create emit moved into
    # services/tag_service.py::create_tag_for_scope. The scan now follows one
    # level of delegation into services/, so the entry is redundant and was
    # removed rather than left to rot. The entries below that cite delegation
    # stay explicit on purpose: they route through generically-named helpers
    # (`dispatch`, `publish_draft`, `atomic_swap`) where a bare name match
    # would be doing the work, and a documented exemption is worth more than
    # an accidental one.)
    # Read-only batch relation-target lookup — resolves a POST body of ids to
    # display labels (permission-filtered). No graph mutation.
    ("backend/app/api/entry_lookup.py", "relation_target_lookup"),
    # Email verification flow — these mutate ``User.email_verified`` (and the
    # OTP slot on ``User.preferences``) via ``consume_verification_code`` /
    # ``create_verification_request`` but do NOT emit ChangeEvents. Auth
    # surface lives outside the audited resource graph (mirrors the
    # forgot-password / reset-password precedent).
    ("backend/app/api/auth.py", "verify_email"),
    ("backend/app/api/auth.py", "resend_verification"),
    # Short-lived WS ticket mint — no graph mutation; auth handshake only.
    ("backend/app/api/auth.py", "mint_websocket_ticket"),
    # Voice-input session mint — same shape as the WS ticket: returns a
    # short-lived vendor client secret and mutates no audited resource. Each
    # mint is recorded as a structured audit log line (who, which workspace's
    # key, which model) in agentive/services/speech/service.py::mint_session.
    ("backend/app/agentive/api/speech.py", "create_speech_session"),
    # feed_entries wrappers thinly delegate to api.entries.{create,update,delete}_entry
    # which ALREADY emit. Re-emitting here would double-count.
    ("backend/app/api/feed.py", "create_feed_entry"),
    ("backend/app/api/feed.py", "update_feed_entry"),
    ("backend/app/api/feed.py", "delete_feed_entry"),
    # log-only / non-audited high-frequency endpoints (Phase 1 D-06 convention).
    ("backend/app/agentive/api/proactive.py", "proactive_log_push"),
    # Deprecated 410-redirect — no graph mutation (returns 410 Gone-style redirect).
    ("backend/app/agentive/api/proactive.py", "proactive_push_deprecated_redirect"),
    # heartbeat — high-frequency; allow-listed to avoid log explosion. The
    # initial register / unregister DO emit (agent_config.register / .update).
    ("backend/app/agentive/api/uplink.py", "agent_heartbeat"),
    ("backend/app/agentive/api/uplink.py", "agent_heartbeat_system"),
    # Chat dispatch is non-mutation (turn-routing facade) — message persistence
    # happens via the connector dispatch flow which already emits its own events.
    ("backend/app/agentive/api/chat.py", "post_agentive_chat_message"),
    # Tool dispatch facade — the tool itself emits events on resources it touches
    # (callable tools land in Phase 6).
    ("backend/app/agentive/api/agent_tools.py", "execute_tool_endpoint"),
    # App-operation dispatch facade — the declared operation implementation owns
    # its domain mutation event; the broker adds a RunStep receipt, not a second
    # ChangeEvent.
    ("backend/app/api/app_extensions.py", "invoke_operation"),
    # Read-only bounded QuerySpec facade; broker receipts the query but no
    # resource mutation or ChangeEvent occurs.
    ("backend/app/api/query_spec.py", "execute_query_spec_endpoint"),
    # Governed-query POST facades are read-only despite using POST for typed
    # request bodies; neither endpoint mutates audited resource state.
    ("backend/app/api/capabilities.py", "post_query"),
    ("backend/app/api/capabilities.py", "invoke_extension_query"),
    # Channel-resolution / WhatsApp initiation are read-style handshake (no
    # ChannelIdentity row mutation here — the actual mutation paths
    # (create_channel_identity, verify_channel_identity, whatsapp_verify_otp,
    # verify_link_token) DO emit channel_identity.{create,verify}).
    ("backend/app/agentive/api/channels.py", "resolve_identity"),
    ("backend/app/agentive/api/channels.py", "whatsapp_verify_initiate"),
    # Conversation context lives in agentive scope as ephemeral per-turn state;
    # not audited per Phase 2 scope.
    ("backend/app/agentive/api/conversations.py", "create_conversation_context"),
    ("backend/app/agentive/api/conversations.py", "patch_conversation_context"),
    ("backend/app/agentive/api/conversations.py", "delete_conversation_context"),
    # Auth surface — no ChangeEvent per Phase 2 scope (same precedent as
    # verify_email / resend_verification above).
    ("backend/app/api/auth.py", "forgot_password"),
    ("backend/app/api/auth.py", "reset_password"),
    # ContentProfile draft lifecycle — delegates to atomic_swap which emits
    # content_profile.publish / .discard transitively (single-emission idiom).
    ("backend/app/api/content_profiles.py", "fork_content_profile_draft"),
    ("backend/app/api/content_profiles.py", "publish_content_profile_draft"),
    ("backend/app/api/content_profiles.py", "discard_content_profile_draft"),
    # Read-style operations — no resource state mutation.
    ("backend/app/api/content_profiles.py", "diff_content_profile"),
    ("backend/app/api/content_profiles.py", "preview_update_content_profile"),
    # Import merges a library package into an attached CP; the merge itself
    # calls publish_draft / merge helpers that already emit (single-emission).
    ("backend/app/api/content_profiles.py", "import_content_profile"),
    # Connector sync — sync_runtime emits sync.* events per sync iteration
    # (I-SYNC-01). Re-emitting in the HTTP handler would double-count.
    ("backend/app/api/connectors.py", "sync_connector"),
    # Track create should emit track.create — tracked in CHA-EMIT-DRIFT-01.
    # Allowlisted until the full mutation-emit sweep lands.
    ("backend/app/api/tracks.py", "create_track"),
    ("backend/app/api/tracks.py", "post_promote_scratch_entry"),
    # Approval handlers emit approval.approve / approval.reject via
    # approval_executor — adding here is double-emission.
    ("backend/app/api/approvals.py", "approve_approval"),
    ("backend/app/api/approvals.py", "reject_approval_endpoint"),
    # AI chat thread operations — thread lifecycle events deferred to Phase
    # 14 (Chat App) per ROADMAP scope boundary. Allowlisted pending
    # CHA-EMIT-DRIFT-01 sweep.
    ("backend/app/api/ai_chat.py", "create_thread"),
    ("backend/app/api/ai_chat.py", "rename_thread"),
    ("backend/app/api/ai_chat.py", "delete_thread"),
    ("backend/app/api/ai_chat.py", "send_message"),
    ("backend/app/api/ai_chat.py", "cancel_thread"),
    ("backend/app/api/ai_chat.py", "append_system_message"),
    # UI-only ephemeral state — pin toggles and scope selection are not
    # audited resource mutations; no ChangeEvent semantics.
    ("backend/app/api/users.py", "toggle_my_pin"),
    ("backend/app/api/users.py", "set_my_scope"),
    # Access / exclusion handlers — should emit access.granted/revoked;
    # tracked in CHA-EMIT-DRIFT-01. Allowlisted pending sweep.
    ("backend/app/api/access.py", "add_space_exclusion"),
    ("backend/app/api/access.py", "remove_space_exclusion"),
    ("backend/app/api/access.py", "add_entry_collaborator"),
    ("backend/app/api/access.py", "remove_entry_collaborator"),
    ("backend/app/api/access.py", "add_entry_exclusion"),
    ("backend/app/api/access.py", "remove_entry_exclusion"),
    # App lifecycle — lifecycle.install / .pause / .resume / .uninstall already
    # emit app.installed / app.paused / app.resumed / app.uninstalled / app.force_
    # uninstalled via services/app_lifecycle.py. Re-emitting in HTTP handlers
    # would break D-05 single-emission. Allowlisted because the emission
    # happens transitively via the service layer (same as publish_draft pattern).
    ("backend/app/api/apps.py", "create_app"),
    ("backend/app/api/apps.py", "install_app_endpoint"),
    ("backend/app/api/apps.py", "finalize_install_endpoint"),
    ("backend/app/api/apps.py", "patch_app_settings"),
    ("backend/app/api/apps.py", "update_from_library_endpoint"),
    ("backend/app/api/apps.py", "pause_app_endpoint"),
    ("backend/app/api/apps.py", "resume_app_endpoint"),
    ("backend/app/api/apps.py", "uninstall_app_endpoint"),
    # Attachments — upload handlers delegate to storage pipeline which emits
    # attachment.* events via the post-save hooks. Tracked in CHA-EMIT-DRIFT-01.
    ("backend/app/api/attachments.py", "upload_attachment"),
    ("backend/app/api/attachments.py", "upload_attachments_batch"),
    ("backend/app/api/attachments.py", "upload_chat_attachment"),
    ("backend/app/api/attachments.py", "reprocess_attachment_metadata"),
    ("backend/app/api/attachments.py", "start_chunked_upload"),
    ("backend/app/api/attachments.py", "append_chunked_upload_part"),
    ("backend/app/api/attachments.py", "complete_chunked_upload"),
    ("backend/app/api/attachments.py", "cancel_chunked_upload"),
    # Agent preferences — ephemeral UI state, no audited resource mutation.
    ("backend/app/api/agent_preferences.py", "put_agent_preference"),
    # Retrieval — read-only semantic search endpoint; no mutation.
    ("backend/app/api/retrieve.py", "retrieve"),
    # Policy explanation uses POST for a structured request but performs only a
    # dry policy evaluation.
    ("backend/app/api/policies.py", "explain_action"),
    # F3 entitlement projection owns persistence in services/entitlements.py.
    # ChangeEvent vocabulary for billing projections is deferred with the wider
    # entitlement audit surface; avoid double-emitting App pause events here.
    ("backend/app/api/entitlements.py", "post_grant_entitlement"),
    ("backend/app/api/entitlements.py", "post_revoke_entitlement"),
    # Invitations — create/accept/decline/revoke emit from
    # services/invitations.py (_emit_invitation_change). API is thin wrapper.
    ("backend/app/api/invitations.py", "post_create_invitation"),
    ("backend/app/api/invitations.py", "delete_invitation"),
    ("backend/app/api/invitations.py", "delete_resource_invitation"),
    ("backend/app/api/invitations.py", "post_accept_invitation"),
    ("backend/app/api/invitations.py", "post_decline_invitation"),
    ("backend/app/api/invitations.py", "post_create_space_invitation"),
    ("backend/app/api/invitations.py", "post_create_track_invitation"),
    ("backend/app/api/invitations.py", "post_create_entry_invitation"),
    # Share links — mint/revoke/redeem emit from services/share_links.py.
    ("backend/app/api/shares.py", "mint_space_link"),
    ("backend/app/api/shares.py", "mint_track_link"),
    ("backend/app/api/shares.py", "mint_entry_link"),
    ("backend/app/api/shares.py", "revoke_link"),
    ("backend/app/api/shares.py", "redeem_link"),
    # App/track collaborator + exclusion — emit from services/sharing.py.
    ("backend/app/api/apps.py", "add_app_collaborator"),
    ("backend/app/api/apps.py", "remove_app_collaborator"),
    ("backend/app/api/apps.py", "update_app_collaborator_role"),
    ("backend/app/api/tracks.py", "add_collaborator"),
    ("backend/app/api/tracks.py", "remove_collaborator"),
    ("backend/app/api/tracks.py", "update_collaborator_role"),
    ("backend/app/api/tracks.py", "add_exclusion"),
    ("backend/app/api/tracks.py", "remove_exclusion"),
    # Entry collaborator role-update — delegates to services/sharing.py
    # update_collaborator_role which emits {track|app|entry}.collaborator_role_update
    # (single-emission idiom, same precedent as add_entry_collaborator above).
    ("backend/app/api/access.py", "update_entry_collaborator_role"),
    # Workspace storage recalculation — admin/internal operation; not audited.
    ("backend/app/api/workspaces.py", "recalculate_workspace_storage_usage"),
    # Staging token lifecycle — staging.bless / .revoke / .approve already
    # emit via staging_executors.py transitively (same as approval pattern).
    ("backend/app/agentive/api/staging.py", "bless_token_endpoint"),
    ("backend/app/agentive/api/staging.py", "revoke_token_endpoint"),
    ("backend/app/agentive/api/staging.py", "text_approve_endpoint"),
    # Rollback undoes staged writes via services/mutation_rollback; ChangeEvents
    # for the original mutations already exist. Transcript persistence is
    # chat-envelope metadata, not an audited graph mutation (same precedent as
    # bless/revoke above).
    ("backend/app/agentive/api/staging.py", "rollback_token_endpoint"),
    # Answering a resident clarifying question clears an ephemeral marker on
    # ChatThread (``pending_question``) and nothing else — no audited resource
    # changes, so there is no resource event to emit. Same precedent as the
    # bless/revoke endpoints above: chat-envelope metadata, not a graph
    # mutation. The user's actual answer reaches the model as an ordinary chat
    # message, which the chat flow persists on its own path.
    ("backend/app/agentive/api/questions.py", "answer_question_endpoint"),
    # Phase 32 batch install — delegates to services/app_batch_install.batch_install
    # which calls services/app_lifecycle.install_app per item; install_app emits
    # app.installed at step 12. Re-emitting here would double-count (same
    # precedent as install_app_endpoint above).
    ("backend/app/api/apps_batch_install.py", "batch_install_apps"),
    # Phase 30 Wave D (DR-30-02) — entry.precompute runs the bound hook tool
    # and returns its output as a candidate custom_fields patch. No graph
    # mutation in this handler; the caller decides whether to PUT the patch
    # (which goes through update_entry → emits entry.update).
    ("backend/app/api/entries_precompute.py", "precompute_entry"),
    # Phase 30 Wave D (DR-30-01) — generic bundle-tool dispatch facade.
    # Tools that touch audited resources emit their own ChangeEvents via
    # the underlying service layer (same precedent as execute_tool_endpoint).
    ("backend/app/api/tools.py", "call_tool"),
    # Phase 30 Wave D (DR-30-02) — REFERENCES edge link/unlink. Mutation
    # is real but the entry.relation.{link,unlink} emit lands with the
    # CHA-EMIT-DRIFT-01 sweep (same precedent as access.add_entry_collaborator).
    ("backend/app/api/entry_relations.py", "link_entry_relation"),
    ("backend/app/api/entry_relations.py", "unlink_entry_relation"),
    # Phase 30 Wave D (DR-30-02) — transform_entry creates a new entry in
    # ``to_track`` via the transform hook; the new entry's entry.create
    # emit fires through Entry.create → downstream emit (same precedent as
    # Entry.create downstream emit). Re-emitting here would double-count.
    ("backend/app/api/entries_transform.py", "transform_entry"),
    # Phase 30 Wave D (DR-30-02) — public share mint delegates to
    # services/share_links.py:mint_share_link which emits the
    # share_link.create event (single-emission idiom, same precedent as
    # shares.mint_entry_link).
    ("backend/app/api/entries_public_share.py", "mint_public_share"),
    # OAuth consent-URL handshake. Returns Intuit / Google consent URL and a
    # signed CSRF state token — no Connector mutation here. The matching
    # ``*_oauth_callback`` handlers DO emit connector.{create,update} after
    # exchanging the auth code (same precedent as channels.resolve_identity).
    ("backend/app/agentive/api/connectors.py", "post_quickbooks_authorize"),
    ("backend/app/agentive/api/connectors.py", "post_gmail_oauth_start"),
    # M3b-1 OAuth consent (approve) — mints a jvspatial OAuth ``AuthorizationCode``
    # Object via the authorization server. Auth-code / token lifecycle is audited
    # by the jvspatial OAuth layer (the code/token records themselves), NOT by
    # integral graph ChangeEvents — the code is not a graph entity (no Node, no
    # cascade, no permission resolution). Same precedent as the auth-surface
    # entries (verify_email / forgot_password) and the connector OAuth handshakes
    # above.
    ("backend/app/api/oauth_consent.py", "approve_consent"),
    # M3c-2 connected-agents revoke — deactivates the caller's OAuth
    # ``OAuthRefreshToken`` Objects via refresh_store.revoke. Refresh-token grant
    # lifecycle is audited by the jvspatial OAuth refresh_store (the token
    # records), NOT by integral graph ChangeEvents — refresh-token grants are not
    # graph entities. Same precedent as approve_consent above.
    ("backend/app/api/users.py", "revoke_connected_agent"),
    # Watch / unwatch — personal subscription toggles (a WATCHES edge between
    # the caller and a Track/Entry). UI-only ephemeral preference state, not an
    # audited resource mutation. Same precedent as users.toggle_my_pin /
    # set_my_scope above.
    ("backend/app/api/tracks.py", "watch_track"),
    ("backend/app/api/tracks.py", "unwatch_track"),
    ("backend/app/api/entries.py", "watch_entry"),
    ("backend/app/api/entries.py", "unwatch_entry"),
    # AI chat agent turn — turn-routing facade that streams a response; message
    # persistence happens via the chat turn registry / provider flow, not here.
    # Same precedent as ai_chat.send_message and chat.post_agentive_chat_message.
    ("backend/app/api/ai_chat.py", "agent_turn"),
    # BYOK model-credential validation — read-only: validates an API key and
    # returns the result without persisting anything. Same precedent as the
    # read-style handshake entries (channels.resolve_identity).
    ("backend/app/api/model_credentials.py", "validate_my_model_credential"),
    # /me/invitations accept + decline — thin wrappers over
    # services/invitations.py accept_invitation_by_id / decline_invitation_by_id,
    # which emit the workspace.invitation_{accept,decline} ChangeEvents via
    # _emit_invitation_change (single-emission idiom). Same precedent as the
    # invitations.py post_accept_invitation / post_decline_invitation entries.
    ("backend/app/api/shared_with_me.py", "post_accept_my_invitation"),
    ("backend/app/api/shared_with_me.py", "post_decline_my_invitation"),
}


def _is_mutation_decorator(decorator: ast.expr) -> bool:
    """Match @endpoint(..., methods=[POST|PUT|PATCH|DELETE]) and @router.{post,patch,delete,put}."""
    if isinstance(decorator, ast.Call):
        # @router.post(...) — Attribute access on Call.func
        if isinstance(decorator.func, ast.Attribute):
            if decorator.func.attr in ("post", "patch", "delete", "put"):
                return True
        # @endpoint(..., methods=[...])
        if isinstance(decorator.func, ast.Name) and decorator.func.id == "endpoint":
            for kw in decorator.keywords:
                if kw.arg == "methods" and isinstance(kw.value, ast.List):
                    methods = {
                        elt.value
                        for elt in kw.value.elts
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                    }
                    if methods & {"POST", "PUT", "PATCH", "DELETE"}:
                        return True
    return False


def _called_names(func: ast.AST) -> set[str]:
    """Every function name this body calls, by bare name or attribute tail."""
    names: set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return names


def _emitting_service_functions(repo_root: Path) -> set[str]:
    """Names of service functions that themselves call ``emit_change_event``.

    The I-CRUD-01 ratchet moves graph writes out of ``api/`` and into
    ``services/`` so HTTP handlers and agentive staging executors share one
    write path — and the emit travels with the write. A handler-body-only scan
    reads that correct refactor as a D-05 regression: ``create_entry_type``
    stopped calling ``emit_change_event`` because
    ``entry_type_service.create_entry_type_for_track`` now does.

    Following one level of delegation is what keeps the two invariants from
    fighting. It is a NAME match against functions that provably emit, not a
    blanket exemption: a handler that delegates to something which does not
    emit still fails, which is the property this test exists for.
    """
    emitting: set[str] = set()
    for scope in ("services", "agentive"):
        for py_path in (repo_root / "backend" / "app" / scope).rglob("*.py"):
            try:
                tree = ast.parse(py_path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                    continue
                if "emit_change_event" in _called_names(node):
                    emitting.add(node.name)
    emitting.discard("emit_change_event")
    return emitting


def _body_calls_emit(
    func: ast.AsyncFunctionDef, emitting_helpers: set[str] | None = None
) -> bool:
    """True iff the handler emits directly, or delegates to something that does."""
    called = _called_names(func)
    if "emit_change_event" in called:
        return True
    if emitting_helpers and called & emitting_helpers:
        return True
    return False


def test_no_bypass_paths_in_core_api():
    """No mutation handler in app/api/ or app/agentive/api/ skips emit_change_event."""
    repo_root = Path(__file__).resolve().parents[2]
    scopes = [
        repo_root / "backend" / "app" / "api",
        repo_root / "backend" / "app" / "agentive" / "api",
    ]
    offending: list[str] = []
    emitting_helpers = _emitting_service_functions(repo_root)

    for scope in scopes:
        for py_path in scope.rglob("*.py"):
            # Normalize to forward slashes so Windows paths match ALLOW_LIST.
            rel = py_path.relative_to(repo_root).as_posix()
            try:
                tree = ast.parse(py_path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.AsyncFunctionDef):
                    continue
                if not any(_is_mutation_decorator(d) for d in node.decorator_list):
                    continue
                if (rel, node.name) in ALLOW_LIST:
                    continue
                if not _body_calls_emit(node, emitting_helpers):
                    offending.append(f"{rel}::{node.name}")

    assert not offending, (
        "D-05 regression — mutation handler missing emit_change_event call:\n"
        + "\n".join(offending)
    )


def test_allow_list_files_exist():
    """Every entry in ALLOW_LIST points to a real (file, function). Catches stale entries."""
    repo_root = Path(__file__).resolve().parents[2]
    for rel, fn_name in ALLOW_LIST:
        py_path = repo_root / rel
        assert (
            py_path.exists()
        ), f"ALLOW_LIST entry {rel}::{fn_name} — file does not exist"
        tree = ast.parse(py_path.read_text(encoding="utf-8"))
        names = {n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}
        assert (
            fn_name in names
        ), f"ALLOW_LIST entry {rel}::{fn_name} — function does not exist"
