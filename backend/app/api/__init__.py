"""API endpoints for Integral."""

import importlib

# Register all core @endpoint modules (side effect: route registration)
# Agentive routes register via main.py at startup.
for _mod in (
    "access",  # Phase 2 — unified collaborator/exclusion/access endpoints
    "admin_packages",  # M11 Phase C1 — admin hot-load + introspection endpoints
    "admin_users",  # Platform admin user management
    "admin_workspaces",  # Platform admin workspace/resource directory
    "agent_preferences",  # Agent-switcher Task 4 — per-user × per-workspace pref
    "ai_chat",  # Plan 06-05 — migrated from include_router to @endpoint
    "attachments",
    "audit_log",  # PROV-04 — Plan 02-02
    "auth",
    "comments",
    "conflicts",  # Plan 06-05 — migrated from include_router to @endpoint
    "connectors",  # Plan 06-05 — migrated from include_router to @endpoint
    "operational_models",
    "entries",
    "entries_precompute",  # Phase 30 Wave D (DR-30-02) — generic entry.precompute endpoint
    "entries_public_share",  # Phase 30 Wave D (DR-30-02) — generic public-share + legacy adapter
    "entries_transform",  # Phase 30 Wave D (DR-30-02) — generic entry.transform endpoint
    "entry_relations",  # Phase 30 Wave D (DR-30-02) — generic /entries/{id}/related
    "entry_lookup",  # Batch relation-target label lookup (POST /entry-lookup)
    "entry_types",
    "entitlements",  # F3 Phase One — manual grant/revoke/list
    "events_polling",  # EVT-02 — Plan 02-04
    "feed",
    "invitations",
    "link_preview",
    "meta",
    "mission_control",
    "model_credentials",
    "notification_preferences",  # Phase 9 Plan 09-04 — NOTIF-03 settings panel
    "notifications",
    "oauth_consent",  # M3b-1 — SPA-driven OAuth consent (CORE: completes the unconditional OAuth AS + /api/mcp mount regardless of AGENTIVE_ENABLED)
    "approvals",  # Phase 7 Plan 07-04 — UX-03 approval review surface
    "policies",  # Plan 06-05 — migrated from include_router to @endpoint
    "query_spec",
    "retrieve",  # Phase 4 — POST /api/retrieve (RET-03)
    "retrieval_config",  # Phase 8 Plan 08-04 — GET /api/retrieval/config (SET-06)
    "shared_with_me",  # Phase 5 — /me/shared and /me/invitations
    "shares",  # Phase 4 — signed-in share-link endpoints
    "speech_preferences",  # Chat dictation — per-user voice input settings
    "tags",
    "tools",  # Phase 30 Wave D (DR-30-01) — direct bundle-tool invocation
    "tracks",
    "tracks_public_share",
    "apps",
    "app_extensions",
    "capabilities",  # ADR-012 — catalogue + governed query
    "apps_dashboards",
    "apps_batch_install",  # Phase 32 — POST /api/apps/batch-install
    "apps_skills",  # Phase 30 Wave D (DR-30-01) — generic skill catalogue per bundle
    "users",
    "views",
    "workspaces",
):
    importlib.import_module(f"app.api.{_mod}")
