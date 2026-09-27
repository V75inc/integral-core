# Integral Core substrate use cases

**Snapshot:** 2026-09-26. **Audience:** product architecture, engineering pods, acceptance reviewers. **Purpose:** the complete, code-grounded map of what Integral Core delivers on its substrate — the flows, facilities, and user experiences — with the resident agent as the primary operating surface. This is an inventory of implemented facilities and intended experiences, not a release certification; [CORE_FINISH_STATUS.md](CORE_FINISH_STATUS.md) remains the release-status authority. The companion [improvement plan](CORE_SUBSTRATE_IMPROVEMENT_PLAN.md) turns every gap named here into a work package.

## How to read this inventory

| State | Meaning |
| --- | --- |
| **Implemented** | A Core route, service, and agent tool exist and dispatch. This label says nothing about end-to-end journey coverage. |
| **Tested journey** | Implemented, and a named deterministic contract test or evidence record covers the end-to-end journey (cited inline). |
| **Composed** | The experience depends on agent judgment chaining implemented primitives under a skill SOP. Quality must be proven by a live-model exam, not unit tests. |
| **Partial** | A primitive exists but is materially narrower than the experience needs (for example: counts but no sums, one hop but no multi-hop). |
| **Gap (agent)** | Core substrate supports it, but no governed agent tool binds it. |
| **Gap** | No substrate path exists, or the manifest marks the tool `gap`. |

A tool marked `existing` in [tool_manifest.yaml](../../backend/app/agentive/tool_manifest.yaml) proves a dispatchable primitive, not that the complete user task succeeds.

---

## 1. Foundation inventory

### 1.1 Substrate primitives

| Primitive | What it is | Analogy |
| --- | --- | --- |
| **Workspace** | Personal or organization scope; gates everything inside it. | Tenant |
| **App** | Workspace-scoped container of Tracks, skills, dashboards; carries an attached Operational Model. | Schema / database |
| **Track** | Typed operational collection with its own attached Operational Model. | Table |
| **EntryType** | Record shape under a Track profile (`key`, `name`, `fields[]`); a Track may host several. | Row type |
| **Field** | `text`, `markdown`, `number`, `boolean`, `date`, `datetime`, `select`, `multi_select`, `relation`, `computed`, `file`/`files`, `json`, `member`. | Column |
| **Entry** | A record; lives in a Track via `CONTAINS`; typed via `IS_OF_TYPE`. | Row |
| **Relation** | `relation` field → `REFERENCES` (lookup, `target: entry`) or `ANCHORS` (expansion, `target: track`). The only first-class cross-record pointer; cross-track and cross-App links are legal. | Foreign key / detail table |
| **Tag** | Scoped (workspace/App/Track), hierarchical via `parent_tag_id`; taxonomy groups on the model. | Label |
| **View** | Palette projection of a Track: `table`, `feed`, `kanban`, `calendar`, `gallery`, `wiki`, `composable_list/grid/board/timeline`, `extension_view`. | Saved query + renderer |
| **Dashboard** | App-scoped widget board under `App —CONTAINS→ Dashboards —CATALOGS→ Dashboard`. | Report |
| **Operational Model** | Schema document (entry types, taxonomy, views); library package or attached instance; draft → diff → publish with migrations. | DDL + migrations |
| **Skill** | Declarative SOP. Core `integral_*` skills are global; App/workspace skills form a per-workspace overlay. | Runbook |
| **Routine** | Scheduled or one-shot agent instruction replayed into a chat thread. | Cron job |
| **App operation / trusted tool** | Package-declared command, query, or `tools[]` entry reaching Core only through `ToolContext`. | Stored procedure |

### 1.2 Core skills (16 + base)

All live under [`embedded_integral_action/skills/`](../../agent/agents/integral/integral_agent/actions/integral/embedded_integral_action/skills/) and inherit the base discipline in [`embedded_integral_action/SKILL.md`](../../agent/agents/integral/integral_agent/actions/integral/embedded_integral_action/SKILL.md) (bound identity and scope, propose-never-apply, `[SYSTEM:STAGING-RESOLVED]` handling, mandatory chat links).

| Skill | Owns | Activation | Tools |
| --- | --- | --- | --- |
| `integral_scaffold` | **Flagship** App delivery: discover → clarify → propose → authorize → build → verify → hand off | `use_skill` | 27 |
| `integral_model` | Modeling judgment on existing schema; lookup vs anchor; draft revisions | `use_skill` | 12 |
| `integral_models` | Operational Model/library lifecycle: author, apply, draft, diff, publish, discard | `use_skill` | 13 |
| `integral_filing` | Informal content → facets → destination → staged create | `use_skill`; `integral_file_content` pinned every turn | 4 |
| `integral_entries` | Record CRUD, tags, comments, relation links, transforms | `use_skill`; create pinned | 19 |
| `integral_organize` | Bulk reorg, retag, archive in one batch | `use_skill` | 15 |
| `integral_insights` | Query, count, rank, digest; save a query as a View | `use_skill` | 17 |
| `integral_review` | Periodic synthesis and status rollups; audit review | `use_skill` | 13 |
| `integral_dashboards` | App dashboard compose, suggest, adjust | `use_skill` | 11 |
| `integral_workspace` | Orientation, App/Track CRUD, sharing, access, connector conflicts, workspace tools | `use_skill` | 31 |
| `integral_onboard` | Multi-turn first-run setup, then the scaffold flow | `use_skill` | 16 |
| `integral_scheduling` | Routines: create, list, pause, resume, cancel | `use_skill` | 9 |
| `integral_attachments` | List, read, transcribe, and attach files | `use_skill` | 13 |
| `integral_artifacts` | Session blueprints, checklists, notes | `use_skill` | 3 |
| `integral_navigation` | Every cited object is a clickable link | always-active | 0 |
| `integral_identity` | Acting user | always-active | 1 |

### 1.3 Tool surface

114 manifest tools in 17 domains: **45 read, 67 propose, 2 execute**; 111 `existing`, 3 `gap` (`integral_bulk_move_entries`, `integral_workspace_setup`, `integral_onboard_user`).

| Domain | Count | Representative tools |
| --- | --- | --- |
| A Discovery | 14 | `whoami`, `get_scope`, `get_page_context`, `list_apps/tracks`, `get_track_schema`, `describe_substrate`, `describe_model`, `describe_capabilities`, `governed_query` |
| B Retrieval | 10 | `query` (hybrid), `query_spec`, `query_entries`, `resolve_entry`, `get_related`, `search_cross_track`, `count_entries`, `activity_digest`, `get_digest`, `get_feed` |
| C Entry lifecycle and orchestration | 19 | `create/update/delete_entry`, `file_content`, `propose_design`, `build_approved_design`, `begin/commit/cancel_batch`, `bulk_update/delete_entries`, `link_entries`, `transform_entry`, `ask_user`, artifacts |
| D Tagging and workspace tools | 6 | `add/remove_entry_tag`, `list_tags`, `create_tag`, `list_workspace_tools`, `call_workspace_tool` |
| E Comments | 4 | `add/list/edit/delete_comment` |
| F Schema authoring | 10 | `get_model_draft`, `propose_model_revision`, `diff_model_draft`, `publish_model_draft`, `modify_model`, `author_model`, `apply_model_to_track`, `recommend_customizations` |
| G Views and dashboards | 9 | `list/save/delete_view`, `describe_dashboard_substrate`, `suggest_dashboard`, `create/update/delete_dashboard` |
| H Structure | 9 | `create_app`, `create_app_track`, `create/update/delete_track`, `update/delete_app` |
| I Sharing and access | 10 | `get_access`, `share`, collaborators, exclusions, share links, `invite` |
| J Attachments | 8 | list (entry/track/workspace), `get_attachment_text`, `transcribe_audio`, attach tools |
| K–Q | 15 | notifications, audit log, connector conflicts/sync, `export_view`, routines, `author/update/delete_skill` |

### 1.4 Runtime facilities that wrap every flow

| Facility | Behavior | Anchor |
| --- | --- | --- |
| **Bound principal and scope** | Identity comes from the request, never a tool argument; `X-Integral-Scope` fixes the workspace; policy is evaluated as the acting user on every call. | manifest `privacy_contract`, [`policy_gate.py`](../../backend/app/agentive/tooling/policy_gate.py) |
| **Staging** | Mutating propose tools mint a `StagedChange` (pending → blessed → consumed / revoked / expired); the user blesses a card; the executor applies; a closure marker returns to the agent. Pending and blessed changes are write-through persisted and survive restart (`staging_store`; `test_staging_persistence.py`); terminal token rows are removed from that store; outcome evidence belongs to execution receipts and the conversation record. **Not every propose-class tool stages:** `propose_design` records a design on the thread and an artifact; artifact tools write session notes; `begin/commit/cancel_batch` control a batch; `ask_user` enqueues Prompt Sheet questions; `file_content` returns an unstaged `filing_status: unresolved` payload when it cannot resolve a destination. | [`staging.py`](../../backend/app/agentive/staging.py), [`staging_store.py`](../../backend/app/agentive/staging_store.py), [`staging_executors.py`](../../backend/app/agentive/staging_executors.py) |
| **Batch** | Many staged ops in one approval, with batch tokens `{{app.id}}`, `{{track.id:Name}}`, `{{entry.id:Title}}`. An open, uncommitted batch lives in process memory (`_open_batches`) and does not survive restart. | [`batch_validation.py`](../../backend/app/agentive/batch_validation.py), [`staging.py`](../../backend/app/agentive/staging.py) |
| **Design gate** | A recorded proposal is required before `create_app`; after chat affirmation only the approved-design builder may apply it. | [`dispatch.py`](../../backend/app/agentive/tooling/dispatch.py), [`scaffold_build.py`](../../backend/app/agentive/tooling/scaffold_build.py) |
| **Prompt Sheet** | Sequesters `integral_ask_user` questions and staged writes; blocks non-read tools while open; resumes the turn after answers. | [`prompt_queue.py`](../../backend/app/services/prompt_queue.py), `frontend/src/features/ai-chat/prompt-sheet/` |
| **Page context** | The UI publishes focused App/Track/Entry/View plus visible rows; the agent reads it via `integral_get_page_context`. | [`chat_page_context.py`](../../backend/app/services/chat_page_context.py) |
| **Workspace skill overlay** | Public App skills are composed into the resident per workspace; App-private skills (the default for App-scoped authoring) surface only when that App is in focus — an intentional access boundary. Profile cache invalidated on install/uninstall/author. | [`workspace_agent_profile.py`](../../backend/app/agentive/workspace_agent_profile.py) |
| **Lean tool surfacing** | ~6 presurfaced tools plus pinned writes (`file_content`, `create_entry`, `begin/commit_batch`); long tail via `use_skill` / `find_tool`. | `agent.yaml` |
| **Artifacts** | Conversation-keyed blueprints/checklists (`app_design_blueprint`). | [`artifacts.py`](../../backend/app/agentive/artifacts.py) |
| **Filing personalization** | Similarity-based defaults learned from past accepted filings. | [`personalization.py`](../../backend/app/agentive/personalization.py) |
| **Routines and proactive** | Scheduler-owned routines replay into the same thread; digests and reminders. | `agentive/api/routines.py`, `proactive.py` |
| **Decision ledger** | Terminal approval outcomes recorded to Personal Context. | [`decision_ledger.py`](../../backend/app/agentive/decision_ledger.py) |
| **MCP perimeter** | External agents get the same catalogue, filtered by `integral:read/propose/execute` scope, through the same staging and policy path. No A2A. | [`mcp/server.py`](../../backend/app/agentive/mcp/server.py), [BYOA.md](BYOA.md) |
| **Connectors** | Gmail, GitHub Issues, QuickBooks sync with conflict policies; remote MCP mounts become workspace capabilities. | `agentive/connectors/` |

---

## 2. Flagship: describe an operation, receive a working App

This is the defining experience. A user describes a need in plain language ("I run a small car-rental business; track vehicles, customers, rentals, and service dates"). The target experience is a resident that designs and delivers the operational App the request requires: Tracks, EntryTypes, fields, and any appropriate relations, tags, views, dashboard, operating skills, reminders, seeds, or custom operations. Optional constituents may be absent. The tables below distinguish delivered capabilities from gaps, including the developer handoff needed for executable tools.

### 2.1 Journey

| # | Phase | What happens | Tools | State |
| --- | --- | --- | --- | --- |
| 1 | **Discover** | Read existing Apps (extend rather than duplicate), library profiles, installed capabilities. | `list_apps`, `list_tracks`, `list_models`, `describe_capabilities` | Implemented |
| 2 | **Interpret** | Map nouns to Tracks, attributes to fields, closed states to `select`, relationships to lookup/anchor, decisions to views, procedures to skills, time to routines. | `integral_scaffold` weave patterns, `integral_model` advice | Composed |
| 3 | **Clarify** | Ask only questions that change the result. | `integral_ask_user` (Prompt Sheet) | Implemented |
| 4 | **Propose** | Record the full blueprint and preview it in chat; "design only" stops here with zero writes. | `integral_propose_design` → `app_design_blueprint` artifact | Implemented gate; design quality Composed |
| 5 | **Amend** | Corrections re-propose from the prior body plus deltas. | `propose_design` (`design_amend_required`) | Implemented |
| 6 | **Authorize and build** | Chat affirmation is approval. One call compiles the ordered plan, checks it against the proposal (`plan_differs_from_design`), stages every op in one batch, commits, and returns a receipt. | `integral_build_approved_design` (≤64 ops); manual `begin/commit_batch` for recovery | Implemented for its op set |
| 7 | **Verify** | Read back Apps, Tracks, schema, views, seeds, routines; name what is applied vs verified vs partial. | `list_*`, `get_track_schema`, `query_entries`, `list_routines`, `list_dashboards` | **Composed — SOP only, no verification tool** |
| 8 | **Hand off** | Links, how to operate, reminder cadence, plain limitations. | navigation skill | Composed |
| 9 | **Evolve** | Later changes reuse real IDs; schema draft → diff → publish. | See §5 | Partial |

### 2.2 Constituent coverage — what the flagship can actually fashion

| Constituent | In the proposal | In the one-call build (`_PLAN_TOOLS`) | After build | Notes |
| --- | --- | --- | --- | --- |
| App | Yes | `create_app` | `update_app` | Implemented. Additions to an existing App bind `target_app_id`. |
| Tracks + EntryTypes + fields | Yes | `create_app_track` with inline `entry_types` | draft/patch DSL | Implemented; multiple entry types per Track supported. |
| Relations — lookup (`REFERENCES`) | Yes | Yes; cross-track flags auto-annotated | `link_entries`, patch `add_field` | Implemented |
| Relations — anchor (`ANCHORS`) | Yes | App Track-template registration and source-field binding are not compiled by the plan builder | operational-model compiler / patch DSL | **Partial**; the existing materializer lazily creates a distinct detail Track for each parent Entry. |
| Tags / taxonomy | Mentioned in SOP | **No.** `create_tag` is not a plan tool; inline track compile omits `taxonomy.tag_groups` | `create_tag`, `modify_model add_tag` | **Gap in the flagship** |
| Views | Yes | `save_view` with binding validation (table columns, kanban `group_by`, calendar `dateField`, wiki `parent_field`) | `save_view`, patch `modify_view` | Implemented |
| Seed entries | Yes | `create_entry` with `strict_fields` and named batch refs | entries tools | Implemented |
| Dashboard | Yes | `create_dashboard` with data-source preflight | dashboard tools | Implemented mechanics; widget intelligence is shallow (§6) |
| Operating skills | Yes | `author_skill` → graph `Skill` node, declarative, untrusted | `update/delete_skill` | Implemented. App-private by default (intentional boundary); surfaces when that App is in focus. Workspace-wide visibility needs an explicit choice. |
| Routines / reminders | Yes | `schedule_task` (cron or `run_at` + IANA timezone) | routine tools | Implemented |
| **Custom tools / operations** | SOP silent | **No** | Developer package path only | **Gap as a resident flow.** Only a trusted App package can add `tools[]`/operations; chat can author only SOPs. |
| Reusable library package from the built App | — | — | — | **Gap** (gated expansion D1 in the plan). `export_app` is a data dump, not an installable model/bundle. |
| Access / sharing plan | Optional | No | sharing tools | Composed |
| Verification receipt | — | Applied receipt only | — | **Gap:** no `verified` artifact independent of the model. |

### 2.3 Design judgment the agent must exercise

| User need | Substrate design | Decision rule and proof |
| --- | --- | --- |
| Manage one thing | EntryType and fields in a Track | Separate Track when the thing has its own lifecycle, views, permissions, or is referenced from two or more Tracks. |
| Distinguish states / categories | `select`/`multi_select` field, or Tag | Field for a controlled workflow state (boards group on it); tags for flexible cross-cutting classification. Never group by the platform `status` when a business field exists. |
| Connect things | Lookup (`REFERENCES`) or anchor (`ANCHORS`) | Lookup for independently managed targets, placed on the pointing side; anchor for a heavyweight owned detail collection. Register a reusable App Track template, then lazily provision a separate detail Track per parent Entry. Mixed child kinds → several EntryTypes in that detail Track sliced by `entry_type_keys`. Never both; never reverse-list fields. |
| Work a list | One view per real decision | Board ↔ select; calendar/timeline ↔ date(s); gallery ↔ file; wiki ↔ parent relation + markdown. Feed exists on every Track by default. |
| See the whole App | Dashboard | Widgets only where fields and records support them; every number traceable to a query. |
| Repeat a procedure | Declarative skill | A skill coordinates governed tools; it does not create authority or enforce invariants. |
| Enforce a transition / integrate a system | Declared App operation or trusted tool | Protected state changes need policy, idempotency, receipt, readback — not prose. |
| React in time | Routine | A date field alone never notifies. |

### 2.4 Use cases

| ID | User intent and expected experience | Anchor | State |
| --- | --- | --- | --- |
| B01 | Describe a new domain in plain language; receive a complete blueprint (Tracks, types, fields, relations, tags, views, dashboard, skills, routines, seeds, acceptance checklist). | `integral_scaffold`, `propose_design` | Composed |
| B02 | Correct the blueprint without losing settled decisions. | amend path | Implemented gate |
| B03 | "Design only" → proposal, zero writes. | proposal-only rule | Tested journey (`test_propose_design_dispatch.py`, `test_propose_design_service.py`) |
| B04 | Affirm once; the whole App builds in one governed batch with a receipt. | `build_approved_design` | Tested journey (`test_scaffold_build_plan.py`, `test_operational_app_build.py`); model tool choice Composed |
| B05 | Add Tracks/views/skills to an existing App without duplicating it. | `target_app_id` | Implemented |
| B06 | Build includes a domain tag vocabulary on the new Tracks. | — | **Gap** |
| B07 | Build registers an anchor template; each Project Entry receives its own Project Details Track when materialized. | compiler/materializer exist; build-plan support missing | **Partial** |
| B08 | Build includes a useful dashboard derived from the designed fields. | `create_dashboard` in plan | Partial (§6) |
| B09 | Build includes operating skills that close multi-record loops. | `author_skill` | Implemented; App-private skills reachable only in App focus |
| B10 | Build includes reminders on designed date fields. | `schedule_task` | Implemented |
| B11 | Build includes realistic, typed demo records that exercise each relation. | seeds with batch refs | Implemented |
| B12 | Requirement needs executable behavior (protected transition, external API): the resident says so, specifies the operation/tool, and hands off to the package path. | — | **Gap** |
| B13 | Verify the exact approved design revision against its execution receipt and object mapping; optional omissions are valid, while missing, mismatched, denied, and failed reads remain distinct. | SOP readback only; revision-bound verification tool proposed in W1.5 | **Composed, unenforced** |
| B14 | Start from a library profile instead of greenfield. | `list_models`, `apply_model_to_track` | Implemented |
| B15 | Save a built App as a reusable library package for another workspace. | — | **Gap** |
| B16 | Install / pause / upgrade / uninstall an independently built App. | package lifecycle | Implemented contract; C6 proof open |
| B17 | First-run onboarding interviews the user and provisions a starter workspace. | `integral_onboard` → scaffold | Composed; `onboard_user`/`workspace_setup` are `gap` |
| B18 | Resume a partially built App without duplicates. | `partial_build_requires_repair`, retryable batch | Tested journey (`test_scaffold_recovery_continuation.py`); an open uncommitted batch is lost on restart |

---

## 3. Query and graph inference

Integral's second pillar: one permission-filtered, queryable information space where Apps, Tracks, and Entries interrelate through edges, so the intelligence layer can answer, rank, compare, and infer.

### 3.1 Retrieval instruments

| Instrument | Answers | Real limits |
| --- | --- | --- |
| `integral_query` / `search_cross_track` | "Find anything about X" — graph keyword, semantic (pgvector / Atlas), or hybrid (RRF) | Semantic degrades to keyword with `degraded: true` when no vector driver; returns snippets, not full fields; cannot rank by value. |
| `integral_query_entries` | Filtered rows within a Track (name-tolerant) | Keyword over title+body only; `custom_fields.*` filters with `gte`/`lte`; sort only by `updated_at`/`created_at`/`title`; rows **do** include `custom_fields`; in-memory scan of accessible entries. |
| `integral_query_spec` | Deterministic projection/filter/sort over entry/track/app with provenance (`result_set_id`, receipt); filters and sorts accept qualified `custom_fields.*` keys | **At most one traversal hop**; ≤20 select, ≤8 filters, ≤2 sort, page ≤100, authorized scan ≤1000. |
| `integral_governed_query` | Declared App queries (ADR-012) or bounded Core open scans | Open scans do not walk relations and skip App-domain tracks by design. |
| `integral_count_entries` | Counts grouped by `track`/`status`/`tag`/`entry_type` (and undocumented `date`) | **Count only**; no grouping by custom fields; `since`/`until` on platform timestamps. |
| `integral_get_related` | "What points at this entry?" | **Inbound `REFERENCES` only**; no outbound, no `ANCHORS`, one hop. |
| `integral_activity_digest` / `get_digest` / `get_feed` | Rollup and itemized "what changed" | Period shortcuts `today`/`week`/`month`. |
| `integral_query_audit_log` | Change events over readable resources | — |
| `integral_export_view` | Flat track export | Not a view-engine export. |

There is **no aggregate-by-field tool** (sum/avg/min/max), **no multi-hop traversal**, and **no NL→query planner**; the insights skill answers superlatives by "fetch and reason" over a capped page even though `query_spec` can sort by a business field. Every path is permission-filtered at the source (I-RET-01); semantic candidates are individually re-authorized.

**Query boundary (ADR-012, QuerySpec v1 locked C).** Two query classes exist and must stay distinct: *open bounded Core queries* over Core primitives, and *declared App queries* that an App package publishes as `kind: query` capabilities. The governed query engine excludes App-domain records from open scans, but this package-class restriction is not uniformly enforced across existing generic read paths: QuerySpec and `query_entries` perform permission-filtered reads without that same exclusion. W3.0 in the improvement plan must settle and implement the boundary across all read surfaces before extending them. This gap is separate from resource permissions. In the governed engine, "App domain" means a Track under an App with `installed_package_slug` (`_track_is_app_domain` in [`governed_query/engine.py`](../../backend/app/services/governed_query/engine.py)); workspace-authored Apps — including every App the scaffold builds — stay in the open Core class. Any new calculation or traversal capability must say which class it serves.

### 3.2 Use cases

| ID | User intent | Anchor | State |
| --- | --- | --- | --- |
| D01 | Find entries by text, type, tag, platform dates, and exact business fields in one Track. | `query_entries` | Implemented |
| D02 | Find by meaning across the whole workspace when wording differs. | `query` hybrid/semantic | Implemented when a vector driver is configured |
| D03 | "How many X, broken down by Y" for platform dimensions. | `count_entries` | Implemented |
| D04 | Break down by a **business field** ("deals by stage", "assets by condition"). | — | **Gap** in query tools (dashboards can group by `custom_fields.*`) |
| D05 | Totals and averages ("total pipeline value", "average repair cost this quarter"). | — | **Gap** |
| D06 | Superlatives and rankings ("highest-value deal", "oldest open ticket"). | `query_spec` sort on `custom_fields.*`; skill still teaches fetch-and-reason via `query_entries` | Partial: primitive exists, end-to-end ranking not routed to it |
| D07 | Date-window questions on business dates ("rentals due next week"). | `custom_fields.<date>` + `gte`/`lte`, relative-date values | Partial: the agent must compute windows; skill guidance stale |
| D08 | "What is this connected to?" in both directions, including anchored detail. | `get_related` (inbound), `query_spec` (one hop) | **Partial** |
| D09 | Multi-hop inference ("which customers have rentals on vehicles due for service?"). | — | **Gap** |
| D10 | Cross-App questions over linked records while respecting declared App query surfaces. | governed query + one hop | Partial |
| D11 | Catch-up briefings: what changed, who touched it, what needs attention. | digests, feed, notifications | Implemented |
| D12 | Answers carry links, cited records, scope, limits, and "incomplete" when paging capped. | navigation + provenance | Composed; provenance not chainable |
| D13 | Save a useful answer as a View. | `save_view` | Implemented |
| D14 | No unauthorized record leaks through hits, relations, counts, or dashboards. | policy at source | Implemented contract; cross-surface proof ongoing |
| D15 | Distinguish platform `status`/dates from same-named business fields. | qualified field paths | Tested journey (`contracts/test_c0_expected_outcomes.py`) |
| D16 | Query an installed App's declared read model via UI, HTTP, resident, or MCP with identical results. | capability broker | Implemented contract; deployment proof open |
| D17 | Packaged-App records obey one query boundary on every read surface, including reads made by an App’s own skills. | G29 / W3.0; distinct from D14 resource permissions | **Gap** |

---

## 4. Intelligent capture and filing

The third pillar: the user dumps information informally; the agent understands the current substrate configuration, decomposes the content, proposes the best destination(s), and — when nothing fits — proposes new structure.

### 4.1 How it works today

1. `integral_file_content` is pinned every turn, so filing can start immediately.
2. The skill mandates introspection (`list_tracks`, `get_track_schema`) **this turn**, decomposition into semantic facets (actor, intent, work, relationship, artifact), and one staged card per facet.
3. The stager is deliberately **operational-model agnostic and does not classify**: it resolves a supplied track id/hint (fuzzy title match, small focus bias, optional personalization defaults), requires `type_hint`, normalizes field keys, blocks missing required fields, and stages a **create**. Unresolved input returns `filing_status: unresolved` with no ranked shortlist ([`filing_resolution.py`](../../backend/app/services/filing_resolution.py), [`stagers_filing.py`](../../backend/app/agentive/tooling/stagers_filing.py)).
4. Duplicate checks, domain-skill preference (for example, HR onboarding over a bare record), and destination choice live entirely in the model's reading of the SOP.

### 4.2 Use cases

| ID | User intent | Anchor | State |
| --- | --- | --- | --- |
| C01 | Paste a note, email, or meeting summary; the agent reads current Apps/Tracks/types/fields first. | filing SOP | Composed |
| C02 | One rich message becomes several correctly typed records across Tracks. | facet decomposition | Composed |
| C03 | The agent explains why each destination was chosen and what alternatives it rejected. | — | **Gap:** no structured candidate contract |
| C04 | Existing matching person/project is detected; the agent proposes update or link, not a duplicate. | SOP asks for `query_entries` | Composed, unenforced |
| C05 | New content enriches an existing entry (append, update fields). | `update_entry` separately | **Partial:** filing is create-only |
| C06 | Filed records are linked to related records and tagged in the same approval. | stager accepts undocumented `tags`; no relations | **Partial** |
| C07 | **No suitable destination:** the agent says so, preserves the content, proposes a new EntryType/Track (or App), and files once the structure exists. | — | **Gap** |
| C08 | Content matching an installed App's domain routes to that App's skill. | overlay + SOP prose | Composed |
| C09 | Attach a chat-uploaded file to a new or existing entry in one approval. | attachment tools + batch | Implemented |
| C10 | Read document text or transcribe audio before deciding where the substance belongs. | `get_attachment_text`, `transcribe_audio` | Implemented (provider-dependent) |
| C11 | Filing defaults improve from the user's past accepted filings. | personalization | Partial (simple similarity, not semantic) |
| C12 | Capture now into scratch/personal context; organize later with provenance. | personal-context skills | Partial |

---

## 5. In-place evolution of the substrate

The fourth pillar: the user improves configuration easily, at entry, field, tag, view, and track level, without losing data.

### 5.1 Edit matrix

| Edit | Path | Data migration | State |
| --- | --- | --- | --- |
| Add entry type / view / tag | `modify_model` (add/remove only) or patch DSL | n/a | Implemented |
| Add field | patch `add_field` → diff → publish | `add_field_with_default` migration exists | Implemented |
| Modify field spec (label, options, required) | patch `modify_field` (shallow merge) | — | Implemented for non-destructive edits |
| **Rename field key** | no patch op; migration `rename_field` exists but must be hand-placed in the manifest `migrations[]` | manual | **Partial** |
| **Change field type** | `modify_field` can change `type`; migration `coerce_type` not auto-emitted | manual | **Partial / risky** |
| Rename / prune a select option with data rewrite | `modify_field` + `prune_enum_option` migration | manual | Partial |
| Move a field between entry types | migration `move_field` (same Track) | manual | Partial |
| Reorder fields | none | — | Gap |
| Rename / reparent tags | Core `PUT /tags/{tag_id}` accepts `name` and `parent_tag_id`; no agent tool binds it | — | **Gap (agent)** |
| Merge tags (retag + remove) | none | retag fan-out | Gap |
| Update Track/App metadata | `update_track`, `update_app` | — | Implemented |
| Bulk update / retag / archive entries | `bulk_update_entries`, batch + tag tools | — | Implemented |
| **Move entries between Tracks** | `integral_bulk_move_entries` | — | **Gap** (manifest `gap`) |
| Merge or split Tracks | none | — | Gap |
| Transform an entry into another type (won deal → project) | `transform_entry` (bundle hook) | — | Implemented where an App declares it |
| Heuristic schema advice | `recommend_customizations` (null-rate removals, empty-option fixes, missing table/kanban) | — | Partial |

### 5.2 Use cases

| ID | User intent | State |
| --- | --- | --- |
| E01 | Create, edit, delete, comment on, tag an entry from chat or UI. | Implemented |
| E02 | Create an entry in the current view with its board column or date populated. | Implemented; selection Composed |
| E03 | "Add a priority field to Bugs and a board by it" — one revision card with a diff that states affected entry counts. | Implemented |
| E04 | "Rename `client` to `customer`" or "make budget a number" without losing values. | **Partial** |
| E05 | Add a required field, backfill, then continue the original edit. | Composed; continuation unproven |
| E06 | From an entry, "this needs a field for X" adds it inline to the schema. | Composed (no inline affordance) |
| E07 | Correct a wrong filing by moving or retyping the entry. | **Gap** (no move) |
| E08 | Reorganize a tag vocabulary (rename, merge, nest). | Gap (agent) for rename/nest; Gap for merge |
| E09 | Split an overloaded Track or merge two similar ones. | Gap |
| E10 | Stale revision or concurrent edit produces a conflict, not an overwrite. | Implemented contract |
| E11 | Apply App-specific procedures (protected transitions) instead of editing protected fields. | Implemented contract |

---

## 6. Views and dashboards

### 6.1 Truth

- Views: the full palette is saveable with field-binding validation. Implemented.
- Dashboards: real graph objects; eight widget types shared by backend and frontend — `metric_card`, `metric_row`, `chart_bar`, `chart_line`, `chart_pie`, `activity_digest`, `recent_entries`, `track_breakdown` ([`dashboard_widget_types.py`](../../backend/app/views/dashboard_widget_types.py)).
- Data sources are **counts and grouped counts** only; `group_by` accepts `track`/`status`/`tag`/`entry_type`/`date`/`custom_fields.*`. A line chart is entry count per day — not a numeric series. No sum/avg.
- `integral_suggest_dashboard` is **heuristic on Track count, not schema**: up to three per-Track count tiles; one Track → bar by platform `status` + recent entries; several → track breakdown + activity digest. It does not map select→pie, date→trend, number→KPI sum ([`dashboard_service.py`](../../backend/app/services/dashboard_service.py) `suggest_dashboard_template`).

### 6.2 Use cases

| ID | User intent | State |
| --- | --- | --- |
| F01 | Every Track has a Feed; add only views that serve a decision. | Implemented |
| F02 | Save/revise filtered, sorted, grouped, projected views. | Implemented |
| F03 | "Build the best dashboard for this App" → widgets inferred from the App's fields and relations, each with a stated rationale. | **Partial** — suggester ignores field types |
| F04 | KPI totals and averages ("open pipeline $", "avg days to close"). | **Gap** |
| F05 | Trends of a business value over a business date. | **Gap** |
| F06 | Breakdown by a business select field. | Implemented (explicit `custom_fields.*` group-by) |
| F07 | Adjust an existing dashboard (add, move, remove, rename widgets) without losing edits. | Implemented |
| F08 | Drill from a widget to its contributing records: a filtered view for one Track, or a governed result set for multiple Tracks/declared queries, preserving the metric calculation and scope. | Partial; W5.4 specifies parity and refresh behavior |
| F09 | Query, view, board, calendar, and dashboard agree on the same records at page/date boundaries. | Tested journey ([A09 evidence](evidence/2026-09-21-a09-query-view-parity.md)) |
| F10 | Periodic review or status rollup, optionally saved as a view or scheduled. | Composed |
| F11 | Export a view under the caller's access. | Implemented (flat) |

---

## 7. Supporting experiences

### A. Enter, orient, navigate

| ID | User intent | State |
| --- | --- | --- |
| A01 | Who the agent acts as; which workspace is active. | Implemented |
| A02 | Switch workspace without mixing content. | Implemented |
| A03 | List workspaces, Apps, Tracks with deep links. | Implemented |
| A04 | Ask about "this App/Track/Entry/view" and have page focus resolve it. | Implemented |
| A05 | Discover field, view, widget, operation, skill, and tool affordances before acting. | Implemented |
| A06 | Ask what the agent did this session, with receipt-linked objects. | Composed |
| A07 | See the skills and effective capabilities loaded for this turn, including safe explanations of exclusions. | Gap in turn-specific visibility (Full Sweep U9); Skills settings already lists Core, App, and workspace skills |

### G. Collaboration, automation, extensions

| ID | User intent | State |
| --- | --- | --- |
| G01 | Share an App/Track/Entry or invite; view effective access. | Implemented |
| G02 | Exclude inherited access; revoke grants and links. | Implemented |
| G03 | Approve or reject a staged change or batch with a semantic diff. | Implemented |
| G04 | Recover from duplicate approval, restart, cancellation, partial work. | Tested journey for pending/blessed approvals across restart (`test_staging_persistence.py`); open uncommitted batches are in memory |
| G05 | Schedule, pause, resume, cancel reminders and recurring reviews. | Implemented |
| G06 | Notifications, audit trail, activity feed, connector conflicts. | Implemented |
| G07 | External agent via MCP with identical principal, scope, policy, staging. | Implemented |
| G08 | Install an external App with skills, tools, operations, queries, views, schedules. | Implemented contract; C6 open |
| G09 | Author or update a workspace/App skill from a described procedure. | Implemented |
| G10 | Generate, test, package, and safely activate a new executable tool. | **Gap as end-user flow** |
| G11 | One high-level tool for workspace setup / user onboarding. | Gap (deferred) |
| G12 | Mission Control surfaces pending approvals and inbox. | Gap (Full Sweep U6) |

---

## 8. Cross-cutting experience contract

- **Scope and trust.** All reads and writes bind the authenticated principal and validated workspace; role checks happen at the exact target; packages receive no ambient Core authority.
- **State language.** Proposed, awaiting approval, applying, applied, verified, partial, failed, cancelled are distinct. A card or artifact is never a saved object; "verified" requires independent readback.
- **Evidence.** Answers and deliveries cite affected objects with links, persisted values, scope, limits, and remaining work. Failure never renders as an empty success.
- **Policy parity.** UI, resident, MCP, HTTP, and extension paths converge on the same authorization and operation/query semantics.
- **Graph integrity.** Every persisted Node is rooted via structural edges; relations keep cross-App inference meaningful without bypassing workspace access.
- **Domain neutrality.** Core stays domain-free; App semantics arrive as Operational Models, declared operations/queries, skills, hooks, views, and trusted tools.

## Source index

Reconciled against the [tool manifest](../../backend/app/agentive/tool_manifest.yaml); the [core skills](../../agent/agents/integral/integral_agent/actions/integral/embedded_integral_action/skills/); [`dispatch.py`](../../backend/app/agentive/tooling/dispatch.py), [`scaffold_build.py`](../../backend/app/agentive/tooling/scaffold_build.py), [`bindings.py`](../../backend/app/agentive/tooling/bindings.py), [`stagers_filing.py`](../../backend/app/agentive/tooling/stagers_filing.py); [`query_spec.py`](../../backend/app/agentive/services/query_spec.py), [`agent_insights.py`](../../backend/app/services/agent_insights.py), [`entry_relations.py`](../../backend/app/api/entry_relations.py); [`agent_profile_patches.py`](../../backend/app/services/agent_profile_patches.py), [`operational_model_migrations.py`](../../backend/app/services/operational_model_migrations.py); [`dashboard_service.py`](../../backend/app/services/dashboard_service.py); [`workspace_agent_profile.py`](../../backend/app/agentive/workspace_agent_profile.py); [CORE_FINISH_STATUS.md](CORE_FINISH_STATUS.md), [HARNESS_RUNTIME_SUBSTRATE_GAP_PLAN.md](HARNESS_RUNTIME_SUBSTRATE_GAP_PLAN.md), [WP-06 evidence](evidence/2026-09-22-wp06-resident-flow-contract.md), and the [Full Sweep review](../reviews/2026-09-harness-full-sweep.md).
