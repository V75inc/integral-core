# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **One-shot routines via `run_at`** — `integral_schedule_task` accepts an absolute ISO-8601 `run_at` (omit `cron`) for deferred nudges ("remind me in two minutes"); defaults `max_runs=1`. Inbox / Background Tasks show these as One-shot. Scheduler clears `next_run_at` on dispatch so empty-cron rows cannot double-fire.
- **Inbox Pause / Stop / Remove for scheduled tasks** — Assistant Inbox Scheduled rows can pause, stop (soft-cancel + abort in-flight `origin=routine_task` turn), or hard-remove a `RoutineTask`. New `DELETE /api/agentive/routines/{id}` and staged `integral_delete_routine`; cancel now origin-gates abort so a live human chat on the same thread is left alone. Also fixed `PATCH /agentive/routines/{id}` so the flat JSON body the UI already sends validates (jvspatial no longer nests it under `body`).
- **Prompt Sheet** — unified bottom-sheet sequester for clarifying questions (`integral_ask_user`) and staged-write bless. Durable `ChatThread.prompt_queue`, dispatch tool gate while open, paged UI over the composer (Skip on questions only; Cancel all revokes pending writes). See [docs/superpowers/specs/2026-09-08-prompt-sheet-design.md](docs/superpowers/specs/2026-09-08-prompt-sheet-design.md).
- **Security headers on the SPA** — the static host now sends a Content-Security-Policy, HSTS, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy` and COOP. They previously existed only in `frontend/public/_headers`, a Cloudflare Pages convention nginx never reads, so **no deployed environment had ever sent them**. `connect-src` is derived at container start from the `VITE_API_URL` the image was built with, so it cannot drift from the origin the bundle actually calls and no per-environment secret is required. Inline scripts are allow-listed by sha256 rather than `unsafe-inline`, guarded by `.ci/csp_inline_script_hash_check.sh` (a stale hash breaks the app in the browser only). See [docs/ops/DEPLOY.md](docs/ops/DEPLOY.md).
- **Running-turn visibility across workspaces** — the workspace switcher marks workspaces with a turn in flight, including turns this tab did not start; the trigger reports only work happening in a workspace you are NOT viewing, since the active one is already visible in the chat surface.
- **Staged-change failure reasons** — a blessed write that fails to apply now says why, on every surface that shows it.
- **Per-user turn admission cap** — `MAX_CONCURRENT_TURNS_PER_USER` (default 5) alongside the existing one-turn-per-thread rule; refusals carry `details.reason` (`thread_busy` / `user_turn_limit`). Per worker — see [ADR-005](docs/backend/adr/005-single-worker-until-shared-turn-state.md).
- **Parallel AI chat streams** — per-thread session cache on the frontend (tab switch does not abort background streams); backend turn registry enforces one in-flight turn per thread; real cancel via `jvagent.embed.cancel_interact`; agent-turn + proactive bridge foundation. See [docs/backend/ai-chat.md](docs/backend/ai-chat.md).
- **App dashboards** — `Dashboard` / `Dashboards` graph models, widget palette (KPI, chart, table, …), layout APIs, and `AppDashboardPanel` on the App detail surface.
- **Workspace skill overlay from App bundles** — public skills from installed Apps merge into the embedded jvagent workspace overlay at chat time (see [docs/backend/workspace-agent-profile.md](docs/backend/workspace-agent-profile.md)).
- **Scaffold propose-before-build gate** — `integral_propose_design` (ephemeral), `ChatThread.design_proposed` marker, and `commit_batch` requiring a proposed design plus a user turn before greenfield scaffold builds.
- **App-scoped skill authoring** — workspace skills carry `app_id` / `private`; app delete cascade removes accompanying skills.
- **Staging rollback** — undo consumed staged changes after bless.
- **Durable staging write-through** — pending blesses survive server restart.
- **Chat context enrichment** — `send_message` accepts `entity_refs` and `page_context`; EntryDetail dialog context; mandatory markdown linking for entries, tracks, and apps in chat replies; composer images attachable to entries on demand.
- **Graph cache coherence** — change-event invalidation and agent write-cache management; frontend entry refetch on mutation events.
- **Navigation** — improved integral navigation and entry linking from chat and detail surfaces.
- **Library bundles** — Meetings content-profile package plus plan-rollup walker.
- **Document render engines** — substrate `document_render` (docx / pptx / pdf / markdown) reached from trusted bundle tools via `ToolContext.document_render`.
- **Singular resident harness UX** — onboarding skip persistence and assistant-ui polish (see [docs/product/RESIDENT_HARNESS.md](docs/product/RESIDENT_HARNESS.md)).
- Integral favicon (replaces default Vite icon).
- Unified access API (`GET /{apps|tracks|entries}/{id}/access`), share-links, resource invitations, `/me/shared` aggregators.
- Content Profile draft/publish lifecycle and agent substrate tools (`integral_describe_profile`, `integral_propose_profile_revision`, etc.).
- App Bundles v1: manifest v2, install lifecycle, settings schema, skill registry, cross-App relations.
- Batch install: `POST /api/apps/batch-install` topologically installs N bundles in one call; **Install bundles** dialog on the Apps page (multi-select + per-row label edit).
- Drag-drop ordering — **track ordering within an App** (`CONTAINS.position`, `PATCH /apps/{id}/tracks/order`) and **app ordering within a workspace** (`App.position`, `PATCH /workspaces/{id}/apps/order`); the grouped-by-app `Tracks` view honours `App.position`.
- Generic content-profile catalogue: V75 narrative stripped from library bundles; display-name install labels (`HRM`, `Sales`, `CRM`, …) replace slug-shaped `package.name`; `AppModal` Details auto-populate from the selected profile.
- Demo data seed extended to **every library bundle** — 9 new bundle demos alongside the existing V75 fixtures.

### Changed

- **Scheduling path is RoutineTask-only** — Integral agent sets `proactive_tasks_enabled: false` and `denied_tools: [queue_task]`. Short reminders use `integral_schedule_task(run_at=…)`; jvagent Conversation TaskStore proactive tasks are not ticked in this embed.
- `JsonTableEditor` — structured renderer/editor for array-of-objects JSON fields (`rubric_lines`, `line_items`, `role_effort`, …). Used in the composer, generic read-mode renderer, entry detail panel, and feed card preview. Per-column kind inference (number/boolean/text); "Raw JSON" toggle for nested or irregular shapes.

### Changed

- **jvagent 0.1.8rc12 / jvspatial 0.0.19** — derive-only adjacency (no
  `JVSPATIAL_NODE_EDGE_IDS` toggle; edge collection is the only source of
  truth). Drop the env key from stacks/`.env.example`; delete leftover
  `JVSPATIAL_NODE_EDGE_IDS` from runtime env. Keep
  `deploy/scripts/strip_node_edges.sh` for legacy JSONB scrub.
  `backend/pyproject.toml` pins updated; regenerate `backend/uv.lock` after
  both packages are published (PyPI / TestPyPI).
- **jvagent 0.1.8rc11 / jvspatial 0.0.18** (from 0.1.7 / 0.0.17) — hub-node derive-mode adjacency, neighbour SQL pushdown (`nodes` / `count_nodes` / `nodes_page`), GIN opt-out. Integral defaults `JVSPATIAL_NODE_EDGE_IDS=derive` and `JVSPATIAL_PG_GIN_INDEX=off`; deploy step `deploy/scripts/strip_node_edges.sh`; `.ci/nodes_len_drift_check.sh` blocks `len(await ….nodes(…))`. Hot list paths use `nodes_page` / `limit=`; agentive edge filters pass Edge classes (not ALL_CAPS alias strings). `backend/uv.lock` resolves jvspatial from PyPI and jvagent from TestPyPI.
- **Chat latency safe wins** — shrink always-active lean pins: `integral_identity` keeps only `integral_whoami`; `integral_filing` is no longer always-active (introspection tools surface on skill activation); `pinned_tools: [integral_file_content]` keeps turn-1 filing. Mission Control page context publishes counts + a 5-item sample; global visible-context cap 25 → 8.
- **Produce / Pulse retirement** — artifact composition (tracks, `produce_artifact`, render tool) lives in Sales; plans, status reports, `plan_review`, and cadence `default_schedules` live in Projects. Pulse datasets / `compute_metrics` were not ported.
- **jvagent 0.1.6** (from 0.1.1) — pinned in `backend/requirements.txt`, `backend/pyproject.toml`, `backend/uv.lock`, `backend/Dockerfile`, and CI. Via 0.1.2: interview `for_each` staging, orchestrator skill-grounding and resilience fixes, ReplyAction intro egress fix, security hardening (email send + WhatsApp QR admin gates, inbound SPF/DKIM), log retention + PII redaction in visitor logs. Across 0.1.3–0.1.6 every surface integral imports changed additively only: skills gain behavioural `parameters` (ADR-0037) on both `SkillDoc` and SKILL.md frontmatter, `Interaction` gains `interaction_row_sort_key` (raw-row chronology, so `get_interactions` hydrates only its window instead of the whole conversation), plus new opt-in modules (`egress_gate`, `public_gate`, `mcp_oauth`, `page_context`, `turn_cache` / `turn_state`, `session_context`, voice/avatar/upload endpoints). No removals and no changed signatures on anything integral calls. See [jvagent CHANGELOG](https://github.com/TrueSelph/jvagent/blob/main/CHANGELOG.md).
- **jvspatial 0.0.15** (from 0.0.10) — required by jvagent 0.1.6. Via 0.0.12: Postgres backend, OAuth authorization server, identity-map perf, `find_connected_nodes`. 0.0.13–0.0.15 are sort and pagination correctness fixes: dotted sort paths (`context.started_at`) now resolve on the in-memory path (previously ordered arbitrarily on JsonDB/DynamoDB and on any SQLite/Postgres fallback), records missing the sort field sort **last** in both directions on every backend, `GraphContext.find_page` no longer raises on dotted sort fields nor stops early at the trailing missing-value run, `ObjectPager`'s re-sort routes through `finalize_find_results` (pages could previously repeat or drop records), and Postgres withholds `LIMIT` when the sort cannot be pushed down. Adds `resolve_sort_value`, a `DeferredTaskError` hierarchy, `TaskScheduler.schedule(strict=…)`, and `FileValidator` `hint_mime` — all additive. Regenerated `backend/uv.lock` to match.
- **jvagent 0.1.7 / jvspatial 0.0.17** (from 0.1.6 / 0.0.15) — 0.1.7 hard-pins `jvspatial==0.0.17`, so they move as a pair. Relevant here: Postgres became selectable from `Server` (0.0.16), and `DatabaseConfig` stopped silently discarding values passed by field name (0.0.17) — its aliased `postgres_*` fields lacked `populate_by_name`, so nothing raised and the adapters re-read the same settings from env, making the config object look authoritative without being it. The rest is additive. `backend/uv.lock` regenerated: CI installs with `uv export --frozen`, which fails rather than resolving around a drifted lock.
- **`observation_max_chars` 4000 → 12000, `stale_observation_max_chars` 600 → 2500, `observation_full_recent` 3 → 5** on the orchestrator (`agent/agents/integral/integral_agent/agent.yaml`). jvagent elides tool results past these caps and tells the model to re-run the tool; the defaults were sized for research-shaped turns and starved act-on-what-you-found ones. **Note these apply only where `JVAGENT_UPDATE_MODE=source`** — under `merge` (prod) the persisted action node keeps its existing values, and the server now warns about that at boot.
- **CI paths filter covers `agent/**`**, and the orchestrator config tests carry the `smoke` marker. A descriptor-only change previously produced no CI run at all, and even when CI ran, `-m smoke` collected nothing from the one file that reads `agent.yaml`.
- User-facing primitive renamed **Space → App** (`/api/apps`); graph discriminator remains `WorkspaceApp` (jvagent collision avoidance).
- List endpoints require **`X-Integral-Scope`** workspace header (backend-authoritative scope).
- Windows dev ergonomics — pre-commit substrate hooks run under `bash` with `resolve_python.sh` for venv/python discovery.

### Fixed

- **Response meta bar sometimes blank** — turn-level `step` / `final-content` / `message-finish` that landed after a trailing `message-boundary` were applied to an empty draft and dropped on persist; cold-thread reconcile then wiped any live tally the browser already had. Fold those events onto the last contentful bubble (and patch checkpointed rows), keep local observability when the server row lacks it, read jvagent's `event_type`/`data` metric shape for step extraction, and fall back to `interaction.usage.total_duration_seconds` in the meta bar.
- **A recovered turn no longer reads as a failed one** — a tool failure the orchestrator handled and moved past used to raise the same red banner as a turn that genuinely failed, under an otherwise correct answer. Turn-level errors are now held and resolved at `final`: dropped when an answer lands, emitted when none does. The failure stays visible in the tool disclosure either way.
- **Multi-step turns keep what they read** — `stale_observation_max_chars` (600) truncated earlier tool results once a turn passed `observation_full_recent` (3), so the model lost entry ids it had already fetched and re-queried until it exhausted its budget. "Mark all bugs as high priority" bailed mid-task; at 2500/5 it completes. Measured on the same prompt: 7 tool calls / 177.6k tokens / no result → 6 / 120.6k / staged change.
- **Agent listing payloads** — list tools projected to the fields a listing is for (Track 639 → 167 chars/item, App 944 → 233). Combined with the observation budget above, the benchmark listing question went from 7 calls / 123.2k tokens / a missing answer to 2 calls / 20.5k / complete.
- **Dock chat layout** — the composer was cut off because the thread root's `h-full` ignored the onboarding strip sharing its column (`overflow-hidden` clipped the excess rather than scrolling it), and the newest message bubble sat flush against the header because `turnAnchor="top"` anchors it at exactly 0px. Both are fixed; the anchored turn now carries 32px of room.
- **Readiness probe path in the runbook** — `/health/ready` 404s; the route is `/api/health/ready`. `/health` is jvspatial's built-in liveness route and is NOT under `/api`.
- Content moderation — pin `better-profanity<1` and add fallback filter when conjugation gaps slip through.
- Dashboard `chart_line` widgets use the correct `group_by` field.
- Anchored-track template ContentProfile refreshes on empty-typed read.
- June 2026 QA bug-fix round (Mission Control, breadcrumbs, staging gates, and related UI polish).

### Removed

- **Produce and Pulse library bundles** — first-party App packages retired; keepers folded into Sales and Projects (see Changed).
- **Fly.io + Cloudflare Pages deploy path** — `deploy-backend.yaml`, `deploy-frontend.yaml`, `backend/fly.toml`, `frontend/public/_redirects`, `frontend/public/_headers`. `gointegral.app` was never provisioned and no `FLY_API_TOKEN` / `CLOUDFLARE_*` secret ever existed, so both workflows failed on every push to `main` from the day they landed. What deploys is `.github/workflows/deploy.yml` (registry build + SSH to a Swarm host). The unbuilt topology is kept in [docs/ops/DEPLOY.md](docs/ops/DEPLOY.md) marked **(historical)**.

### Security

- **nanoid GHSA-28wg-ghj8-5hjv** (high) — non-secure generators can loop indefinitely on a negative size. Bumped 5.1.11 → 5.1.16 (and the transitive 3.3.16 → 3.3.18).
- SPA security headers — see Added.
- Agent insight tools apply **workspace gate before** per-user permission cascade (B-AGENT-03).

## [0.1.0] - 2026-05-16

### Changed

- **`Organization` → `Workspace`:** Standalone `Organization` node retired; org membership via `IS_MEMBER_OF` (`admin | member | guest`). Legacy `/api/organizations` removed. Dev/staging: re-seed jvspatial DB when upgrading.

### Removed

- Per-entry `visibility_rule` on API (access follows track/app cascade + collaborators; see [docs/product/ARCHITECTURE.md](docs/product/ARCHITECTURE.md) §9).
- Frontend `/shared` route (redirects to `/`; aggregators remain for Mission Control).

### Added

- Email verification (non-blocking OTP): `POST /auth/verify-email`, `POST /auth/resend-verification`.
- Refresh-token rotation on `/auth/refresh`.

### Notes

- Major graph shape changes: delete or re-seed `JVSPATIAL_DB_PATH` data in dev/staging.
- Historical breaking-change detail previously lived in [backend/README.md](backend/README.md); new entries should be added here.
